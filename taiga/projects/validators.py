# -*- coding: utf-8 -*-
# Copyright (C) 2014-2016 Andrey Antukh <niwi@niwi.nz>
# Copyright (C) 2014-2016 Jesús Espino <jespinog@gmail.com>
# Copyright (C) 2014-2016 David Barragán <bameda@dbarragan.com>
# Copyright (C) 2014-2016 Alejandro Alonso <alejandro.alonso@kaleidos.net>
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU Affero General Public License as
# published by the Free Software Foundation, either version 3 of the
# License, or (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU Affero General Public License for more details.
#
# You should have received a copy of the GNU Affero General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from django.db.models import Q
from django.utils.translation import ugettext as _

from taiga.base.api import serializers
from taiga.base.api import validators
from taiga.base.exceptions import ValidationError
from taiga.base.fields import JsonField
from taiga.base.fields import PgArrayField
from taiga.users.models import User, Role
from taiga.users import filters as user_filters

from .tagging.fields import TagsField

from . import models
from . import services


class DuplicatedNameInProjectValidator:
    def validate_name(self, attrs, source):
        """
        Check the points name is not duplicated in the project on creation
        """
        model = self.opts.model
        qs = None
        # If the object exists:
        if self.object and attrs.get(source, None):
            qs = model.objects.filter(
                project=self.object.project,
                name=attrs[source]).exclude(id=self.object.id)

        if not self.object and attrs.get("project", None) and attrs.get(source, None):
            qs = model.objects.filter(project=attrs["project"], name=attrs[source])

        if qs and qs.exists():
            raise ValidationError(_("Name duplicated for the project"))

        return attrs


class ProjectExistsValidator:
    def validate_project_id(self, attrs, source):
        value = attrs[source]
        if not models.Project.objects.filter(pk=value).exists():
            msg = _("There's no project with that id")
            raise ValidationError(msg)
        return attrs


######################################################
# Custom values for selectors
######################################################

class EpicStatusValidator(DuplicatedNameInProjectValidator, validators.ModelValidator):
    class Meta:
        model = models.EpicStatus


class UserStoryStatusValidator(DuplicatedNameInProjectValidator, validators.ModelValidator):
    class Meta:
        model = models.UserStoryStatus


class PointsValidator(DuplicatedNameInProjectValidator, validators.ModelValidator):
    class Meta:
        model = models.Points


class TaskStatusValidator(DuplicatedNameInProjectValidator, validators.ModelValidator):
    class Meta:
        model = models.TaskStatus


class SeverityValidator(DuplicatedNameInProjectValidator, validators.ModelValidator):
    class Meta:
        model = models.Severity


class PriorityValidator(DuplicatedNameInProjectValidator, validators.ModelValidator):
    class Meta:
        model = models.Priority


class IssueStatusValidator(DuplicatedNameInProjectValidator, validators.ModelValidator):
    class Meta:
        model = models.IssueStatus


class IssueTypeValidator(DuplicatedNameInProjectValidator, validators.ModelValidator):
    class Meta:
        model = models.IssueType


######################################################
# Members
######################################################

class MembershipValidator(validators.ModelValidator):
    email = serializers.EmailField(required=False)
    user = serializers.PrimaryKeyRelatedField(required=False)

    class Meta:
        model = models.Membership

    def _validate_member_doesnt_exist(self, attrs, email):
        project = attrs.get("project", None if self.object is None else self.object.project)
        if project is None:
            return attrs

        qs = models.Membership.objects.all()

        # If self.object is not None, the serializer is in update
        # mode, and for it, it should exclude self.
        if self.object:
            qs = qs.exclude(pk=self.object.pk)

        qs = qs.filter(Q(project_id=project.id, user__email=email) |
                       Q(project_id=project.id, email=email))

        if qs.count() > 0:
            raise ValidationError(_("The user yet exists in the project"))

    def validate_email(self, attrs, source):
        email = attrs.get(source, None)
        if not email:
            return attrs

        self._validate_member_doesnt_exist(attrs, email)

        return attrs

    def validate_user(self, attrs, source):
        user = attrs.get(source, None)
        if not user:
            return attrs

        # If the validation comes from a request let's check the user is a valid contact
        request = self.context.get("request", None)
        if request is not None and request.user.is_authenticated():
            valid_user_ids = request.user.contacts_visible_by_user(request.user).values_list("id", flat=True)
            if user.id not in valid_user_ids:
                raise ValidationError(_("The user must be a valid contact"))

        self._validate_member_doesnt_exist(attrs, user.email)

        return attrs

    def validate_role(self, attrs, source):
        project = attrs.get("project", None if self.object is None else self.object.project)
        if project is None:
            return attrs

        role = attrs[source]

        if project.roles.filter(id=role.id).count() == 0:
            raise ValidationError(_("Invalid role for the project"))

        return attrs

    def validate_is_admin(self, attrs, source):
        project = attrs.get("project", None if self.object is None else self.object.project)
        if project is None:
            return attrs

        if (self.object and self.object.user):
            if self.object.user.id == project.owner_id and not attrs[source]:
                raise ValidationError(_("The project owner must be admin."))

            if not services.project_has_valid_admins(project, exclude_user=self.object.user):
                raise ValidationError(
                    _("At least one user must be an active admin for this project.")
                )

        return attrs

    def validate(self, data):
        user = data.get("user", None)
        email = data.get("email", None)

        # On creating user or email must be included
        if self.object is None and user is None and email is None:
            raise ValidationError(_("Email or user must be set"))

        return data

class MembershipAdminValidator(MembershipValidator):
    class Meta:
        model = models.Membership


class _MemberBulkValidator(validators.Validator):
    email = serializers.EmailField()
    role_id = serializers.IntegerField()


class MembersBulkValidator(ProjectExistsValidator, validators.Validator):
    project_id = serializers.IntegerField()
    bulk_memberships = _MemberBulkValidator(many=True)
    invitation_extra_text = serializers.CharField(required=False, max_length=255)

    def validate_bulk_memberships(self, attrs, source):
        filters = {
            "project__id": attrs["project_id"],
            "id__in": [r["role_id"] for r in attrs["bulk_memberships"]]
        }

        #TODO: comparing by lengh?
        if Role.objects.filter(**filters).count() != len(set(filters["id__in"])):
            raise ValidationError(_("Invalid role ids. All roles must belong to the same project."))

        return attrs


######################################################
# Projects
######################################################

class ProjectValidator(validators.ModelValidator):
    anon_permissions = PgArrayField(required=False)
    public_permissions = PgArrayField(required=False)
    tags = TagsField(default=[], required=False)

    class Meta:
        model = models.Project
        read_only_fields = ("created_date", "modified_date", "slug", "blocked_code", "owner")


######################################################
# Project Templates
######################################################

class ProjectTemplateValidator(validators.ModelValidator):
    default_options = JsonField(required=False, label=_("Default options"))
    us_statuses = JsonField(required=False, label=_("User story's statuses"))
    points = JsonField(required=False, label=_("Points"))
    task_statuses = JsonField(required=False, label=_("Task's statuses"))
    issue_statuses = JsonField(required=False, label=_("Issue's statuses"))
    issue_types = JsonField(required=False, label=_("Issue's types"))
    priorities = JsonField(required=False, label=_("Priorities"))
    severities = JsonField(required=False, label=_("Severities"))
    roles = JsonField(required=False, label=_("Roles"))

    class Meta:
        model = models.ProjectTemplate
        read_only_fields = ("created_date", "modified_date")


######################################################
# Project order bulk serializers
######################################################

class UpdateProjectOrderBulkValidator(ProjectExistsValidator, validators.Validator):
    project_id = serializers.IntegerField()
    order = serializers.IntegerField()
