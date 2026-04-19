from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0005_medicineschedule_escalation_call_id_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='last_logout_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
