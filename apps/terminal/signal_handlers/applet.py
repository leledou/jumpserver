from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver
from django.utils.functional import LazyObject

from accounts.models import Account
from common.signals import django_ready
from common.utils import get_logger
from common.utils.connection import RedisPubSub
from orgs.utils import tmp_to_builtin_org
from users.models import User
from ..models import Applet, AppletHost
from ..tasks import applet_host_generate_accounts
from ..utils import DBPortManager

db_port_manager: DBPortManager
logger = get_logger(__file__)


@receiver(post_save, sender=AppletHost)
def on_applet_host_create(sender, instance, created=False, **kwargs):
    if not created:
        return
    applets = Applet.objects.all()
    instance.applets.set(applets)

    applet_host_change_pub_sub.publish(True)
    if instance.auto_create_accounts:
        applet_host_generate_accounts.delay(instance.id)


@receiver(post_save, sender=User)
def on_user_create_create_account(sender, instance: User, created=False, **kwargs):
    if not created:
        return
    if instance.is_service_account:
        return
    with tmp_to_builtin_org(system=1):
        applet_hosts = AppletHost.objects.all()
        for host in applet_hosts:
            if not host.auto_create_accounts:
                continue
            host.generate_private_accounts_by_usernames([instance.username])


@receiver(post_delete, sender=User)
def on_user_delete_remove_account(sender, instance, **kwargs):
    with tmp_to_builtin_org(system=1):
        applet_hosts = AppletHost.objects.all().values_list('id', flat=True)
        accounts = Account.objects.filter(asset_id__in=applet_hosts, username=instance.username)
        accounts.delete()


@receiver(post_delete, sender=AppletHost)
def on_applet_host_delete(sender, instance, **kwargs):
    applet_host_change_pub_sub.publish(True)


@receiver(post_save, sender=Applet)
def on_applet_create(sender, instance, created=False, **kwargs):
    if not created:
        return
    hosts = AppletHost.objects.all()
    instance.hosts.set(hosts)
    applet_host_change_pub_sub.publish(True)


@receiver(post_delete, sender=Applet)
def on_applet_delete(sender, instance, **kwargs):
    applet_host_change_pub_sub.publish(True)


class AppletHostPubSub(LazyObject):
    def _setup(self):
        self._wrapped = RedisPubSub('fm.applet_host_change')


@receiver(django_ready)
def subscribe_applet_host_change(sender, **kwargs):
    logger.debug("Start subscribe for expire node assets id mapping from memory")

    def on_change(message):
        from terminal.connect_methods import ConnectMethodUtil
        ConnectMethodUtil.refresh_methods()

    applet_host_change_pub_sub.subscribe(on_change)


applet_host_change_pub_sub = AppletHostPubSub()
