# -*- coding: utf-8 -*-
from __future__ import unicode_literals

from django.contrib.postgres.fields import JSONField

from django.db import models, migrations


class Migration(migrations.Migration):

    dependencies = [
        ('userstorage', '0001_initial'),
    ]

    operations = [
        migrations.RunSQL(
            sql='ALTER TABLE userstorage_storageentry ALTER COLUMN value DROP NOT NULL;',
        ),
    ]
