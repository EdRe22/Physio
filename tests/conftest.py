import pytest
from django.test import Client
from django_otp import DEVICE_ID_SESSION_KEY
from django_otp.oath import TOTP
from django_otp.plugins.otp_totp.models import TOTPDevice

from accounts.models import Therapist
from core import tenancy
from core.models import Tenant

DBS = ["default", "t_alpha", "t_beta", "t_zeta"]


def pytest_collection_modifyitems(items):
    marker = pytest.mark.django_db(databases=DBS)
    for item in items:
        item.add_marker(marker)


@pytest.fixture
def alpha():
    return Tenant.provision("alpha", "Therapeut Alpha")


@pytest.fixture
def beta():
    return Tenant.provision("beta", "Therapeut Beta")


def make_user(tenant, name, password="ein-langes-passwort-123"):
    return Therapist.objects.create_user(username=name, password=password, tenant=tenant)


@pytest.fixture
def user_alpha(alpha):
    return make_user(alpha, "alpha_user")


@pytest.fixture
def user_beta(beta):
    return make_user(beta, "beta_user")


def totp_token(device):
    return f"{TOTP(device.bin_key, device.step, device.t0, device.digits, device.drift).token():06d}"


def verified_client(user):
    """Client mit Passwort-Login und bestätigtem zweiten Faktor."""
    device = TOTPDevice.objects.create(user=user, name="default", confirmed=True)
    client = Client()
    client.force_login(user)
    session = client.session
    session[DEVICE_ID_SESSION_KEY] = device.persistent_id
    session.save()
    return client


@pytest.fixture
def client_alpha(user_alpha):
    return verified_client(user_alpha)


@pytest.fixture
def client_beta(user_beta):
    return verified_client(user_beta)


@pytest.fixture
def in_alpha(alpha):
    with tenancy.tenant_context(alpha):
        yield alpha


@pytest.fixture
def in_beta(beta):
    with tenancy.tenant_context(beta):
        yield beta
