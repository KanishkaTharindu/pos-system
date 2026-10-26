from django.contrib.auth import get_user_model
from django.db.models import Q

from .models import Notification, UserPreference


def notify_user(user, *, kind, title, body, target=""):
    return Notification.objects.create(
        recipient=user,
        kind=kind,
        title=title[:160],
        body=body[:500],
        target=target[:250],
    )


def notify_roles(*, roles, kind, title, body, target="", preference=None, exclude_user=None):
    User = get_user_model()
    users = User.objects.filter(Q(is_superuser=True) | Q(groups__name__in=roles)).distinct()
    if exclude_user is not None:
        users = users.exclude(pk=exclude_user.pk)
    preference_flags = dict(UserPreference.objects.filter(user__in=users).values_list("user_id", preference)) if preference else {}
    notifications = []
    for user in users:
        if preference and preference_flags.get(user.pk, True) is False:
            continue
        notifications.append(Notification(
            recipient=user,
            kind=kind,
            title=title[:160],
            body=body[:500],
            target=target[:250],
        ))
    return Notification.objects.bulk_create(notifications)
