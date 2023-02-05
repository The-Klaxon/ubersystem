from datetime import datetime

import cherrypy
import pytest
import bcrypt
import pytz
from cherrypy import HTTPError

from tests.uber.conftest import csrf_token
from uber.api import auth_by_token, auth_by_session, api_auth, all_api_auth
from uber.config import c
from uber.models import AdminAccount, Attendee, ApiToken, Session, AccessGroup
from uber.utils import check


assert csrf_token


VALID_API_TOKEN = '39074db3-9295-447a-b831-8cbaa93a0522'


@pytest.fixture()
def session():
    with Session() as session:
        yield session


@pytest.fixture()
def admin_account(monkeypatch, session):
    attendee = Attendee(
        placeholder=True,
        first_name='Test',
        last_name='Developer',
        email='magfest@example.com',
        badge_type=c.ATTENDEE_BADGE,
    )
    session.add(attendee)

    test_developer_account = AdminAccount(
        attendee=attendee,
        hashed=bcrypt.hashpw('magfest'.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
    )

    session.add(test_developer_account)
    session.commit()
    session.refresh(test_developer_account)
    monkeypatch.setitem(cherrypy.session, 'account_id', test_developer_account.id)
    yield test_developer_account
    cherrypy.session['account_id'] = None
    session.delete(test_developer_account)
    session.delete(attendee)

@pytest.fixture()
def api_access_groups(session):
    api_read_group = AccessGroup(
        name="API Read",
        read_only_access={"api": "5"}
    )
    api_update_group = AccessGroup(
        name="API Update",
        access={"api": "1"}
    )
    api_create_group = AccessGroup(
        name="API Create",
        access={"api": "2"}
    )
    api_delete_group = AccessGroup(
        name="API Delete",
        access={"api": "5"}
    )
    session.add(api_read_group)
    session.add(api_update_group)
    session.add(api_create_group)
    session.add(api_delete_group)
    session.commit()
    session.refresh(api_read_group)
    session.refresh(api_update_group)
    session.refresh(api_create_group)
    session.refresh(api_delete_group)
    yield {
        c.API_READ: api_read_group,
        c.API_UPDATE: api_update_group,
        c.API_CREATE: api_create_group,
        c.API_DELETE: api_delete_group
    }
    session.delete(api_read_group)
    session.delete(api_update_group)
    session.delete(api_create_group)
    session.delete(api_delete_group)

@pytest.fixture()
def api_token(session, admin_account):
    api_token = ApiToken(
        admin_account=admin_account,
        token=VALID_API_TOKEN)
    session.add(api_token)
    session.commit()
    session.refresh(api_token)
    yield api_token
    session.delete(api_token)

class TestAuthByToken(object):
    ACCESS_ERR = 'Insufficient access for auth token: {}'.format(VALID_API_TOKEN)

    def test_success(self, monkeypatch, api_token):
        monkeypatch.setitem(cherrypy.request.headers, 'X-Auth-Token', api_token.token)
        assert None is auth_by_token(set())

    @pytest.mark.parametrize('token,expected', [
        (None, (401, 'Missing X-Auth-Token header')),
        ('XXXX', (403, 'Invalid auth token, badly formed hexadecimal UUID string: XXXX')),
        ('b6531a2b-eddf-4d08-9afe-0ced6376078c', (
            403, 'Auth token not recognized: b6531a2b-eddf-4d08-9afe-0ced6376078c')),
    ])
    def test_failure(self, monkeypatch, token, expected):
        monkeypatch.setitem(cherrypy.request.headers, 'X-Auth-Token', token)
        assert auth_by_token(set()) == expected

    def test_revoked(self, monkeypatch, session, api_token):
        api_token.revoked_time = datetime.now(pytz.UTC)
        session.commit()
        session.refresh(api_token)
        monkeypatch.setitem(cherrypy.request.headers, 'X-Auth-Token', api_token.token)
        assert auth_by_token(set()) == (403, 'Revoked auth token: {}'.format(api_token.token))

    @pytest.mark.parametrize('token_access,required_access,expected', [
        ([], [], None),
        ([], ['api_read'], (403, ACCESS_ERR)),
        ([], ['api_read', 'api_update'], (403, ACCESS_ERR)),
        ([c.API_READ], [], None),
        ([c.API_READ], ['api_read'], None),
        ([c.API_READ], ['api_read', 'api_update'], (403, ACCESS_ERR)),
        ([c.API_READ, c.API_UPDATE], ['api_read', 'api_update'], None),
    ])
    def test_insufficient_access(self, monkeypatch, session, api_token, token_access, required_access, expected):
        api_token.access = ','.join(map(str, token_access))
        session.commit()
        session.refresh(api_token)
        monkeypatch.setitem(cherrypy.request.headers, 'X-Auth-Token', api_token.token)
        assert auth_by_token(set(required_access)) == expected


class TestAuthBySession(object):
    ACCESS_ERR = 'Insufficient access for admin account'

    def test_success(self, admin_account, csrf_token):
        assert None is auth_by_session(set())

    def test_check_csrf_missing_from_headers(self):
        assert auth_by_session(set()) == (403, 'Your CSRF token is invalid. Please go back and try again.')

    def test_check_csrf_missing_from_session(self, monkeypatch):
        monkeypatch.setitem(cherrypy.request.headers, 'CSRF-Token', 'XXXX')
        assert auth_by_session(set()) == (403, 'Your CSRF token is invalid. Please go back and try again.')

    def test_check_csrf_invalid(self, monkeypatch):
        monkeypatch.setitem(cherrypy.session, 'csrf_token', '74c18d5c-1a92-40f0-b5f3-924d46efafe4')
        monkeypatch.setitem(cherrypy.request.headers, 'CSRF-Token', 'XXXX')
        assert auth_by_session(set()) == (403, 'Your CSRF token is invalid. Please go back and try again.')

    def test_missing_admin_account(self, monkeypatch):
        monkeypatch.setitem(cherrypy.session, 'csrf_token', '74c18d5c-1a92-40f0-b5f3-924d46efafe4')
        monkeypatch.setitem(cherrypy.request.headers, 'CSRF-Token', '74c18d5c-1a92-40f0-b5f3-924d46efafe4')
        assert auth_by_session(set()) == (403, 'Missing admin account in session')

    def test_invalid_admin_account(self, monkeypatch):
        monkeypatch.setitem(cherrypy.session, 'account_id', '4abd6dd4-8da3-44dc-8074-b2fc1b73185f')
        monkeypatch.setitem(cherrypy.session, 'csrf_token', '74c18d5c-1a92-40f0-b5f3-924d46efafe4')
        monkeypatch.setitem(cherrypy.request.headers, 'CSRF-Token', '74c18d5c-1a92-40f0-b5f3-924d46efafe4')
        assert auth_by_session(set()) == (403, 'Invalid admin account in session')

    @pytest.mark.parametrize('admin_access,required_access,expected', [
        ([], [], None),
        ([], ['api_read'], (403, ACCESS_ERR)),
        ([], ['api_read', 'api_update'], (403, ACCESS_ERR)),
        ([c.API_READ], [], None),
        ([c.API_READ], ['api_read'], None),
        ([c.API_READ], ['api_read', 'api_update'], (403, ACCESS_ERR)),
        ([c.API_READ, c.API_UPDATE], ['api_read', 'api_update'], None),
    ])
    def test_insufficient_access(self, monkeypatch, session, admin_account, api_access_groups, admin_access, required_access, expected):
        admin_account.access_groups = [api_access_groups[x] for x in admin_access]
        session.commit()
        session.refresh(admin_account)
        monkeypatch.setitem(cherrypy.session, 'account_id', admin_account.id)
        monkeypatch.setitem(cherrypy.session, 'csrf_token', '74c18d5c-1a92-40f0-b5f3-924d46efafe4')
        monkeypatch.setitem(cherrypy.request.headers, 'CSRF-Token', '74c18d5c-1a92-40f0-b5f3-924d46efafe4')
        assert auth_by_session(set(required_access)) == expected


class TestApiAuth(object):
    AUTH_BY_SESSION_ERR = 'Missing X-Auth-Token header'
    AUTH_BY_TOKEN_ERR = 'Insufficient access for auth token'

    TEST_REQUIRED_ACCESS = [
        ([], [], False),
        ([], ['api_read'], True),
        ([], ['api_read', 'api_update'], True),
        ([c.API_READ], [], False),
        ([c.API_READ], ['api_read'], False),
        ([c.API_READ], ['api_read', 'api_update'], True),
        ([c.API_READ, c.API_UPDATE], ['api_read', 'api_update'], False),
    ]

    @pytest.mark.parametrize('admin_access,required_access,expected', TEST_REQUIRED_ACCESS)
    def test_api_auth_by_session(self, monkeypatch, session, admin_account, api_access_groups, admin_access, required_access, expected):

        @api_auth(*required_access)
        def _func():
            return 'SUCCESS'

        admin_account.access_groups = [api_access_groups[x] for x in admin_access]
        session.commit()
        session.refresh(admin_account)
        monkeypatch.setitem(cherrypy.session, 'account_id', admin_account.id)
        monkeypatch.setitem(cherrypy.session, 'csrf_token', '74c18d5c-1a92-40f0-b5f3-924d46efafe4')
        monkeypatch.setitem(cherrypy.request.headers, 'CSRF-Token', '74c18d5c-1a92-40f0-b5f3-924d46efafe4')
        if expected:
            with pytest.raises(HTTPError) as error:
                _func()
            assert error.type is HTTPError
            assert error.value.code == 401
            assert error.value._message.startswith(self.AUTH_BY_SESSION_ERR)
        else:
            assert 'SUCCESS' == _func()

    @pytest.mark.parametrize('token_access,required_access,expected', TEST_REQUIRED_ACCESS)
    def test_api_auth_by_token(self, monkeypatch, session, api_token, token_access, required_access, expected):

        @api_auth(*required_access)
        def _func():
            return 'SUCCESS'

        api_token.access = ','.join(map(str, token_access))
        session.commit()
        session.refresh(api_token)
        monkeypatch.setitem(cherrypy.request.headers, 'X-Auth-Token', api_token.token)
        if expected:
            with pytest.raises(HTTPError) as error:
                _func()
            assert error.type is HTTPError
            assert error.value.code == 403
            assert error.value._message.startswith(self.AUTH_BY_TOKEN_ERR)
        else:
            assert 'SUCCESS' == _func()


class TestAllApiAuth(object):
    AUTH_BY_SESSION_ERR = 'Missing X-Auth-Token header'
    AUTH_BY_TOKEN_ERR = 'Insufficient access for auth token'

    TEST_REQUIRED_ACCESS = [
        ([], [], False),
        ([], ['api_read'], True),
        ([], ['api_read', 'api_update'], True),
        ([c.API_READ], [], False),
        ([c.API_READ], ['api_read'], False),
        ([c.API_READ], ['api_read', 'api_update'], True),
        ([c.API_READ, c.API_UPDATE], ['api_read', 'api_update'], False),
    ]

    @pytest.mark.parametrize('admin_access,required_access,expected', TEST_REQUIRED_ACCESS)
    def test_all_api_auth_by_session(
            self, monkeypatch, session, admin_account, api_access_groups, admin_access, required_access, expected):

        @all_api_auth(*required_access)
        class Service(object):
            def func_1(self):
                return 'SUCCESS1'

            def func_2(self):
                return 'SUCCESS2'

        service = Service()

        admin_account.access_groups = [api_access_groups[x] for x in admin_access]
        session.commit()
        session.refresh(admin_account)
        monkeypatch.setitem(cherrypy.session, 'account_id', admin_account.id)
        monkeypatch.setitem(cherrypy.session, 'csrf_token', '74c18d5c-1a92-40f0-b5f3-924d46efafe4')
        monkeypatch.setitem(cherrypy.request.headers, 'CSRF-Token', '74c18d5c-1a92-40f0-b5f3-924d46efafe4')

        if expected:
            with pytest.raises(HTTPError) as error:
                service.func_1()
            assert error.type is HTTPError
            assert error.value.code == 401
            assert error.value._message.startswith(self.AUTH_BY_SESSION_ERR)
        else:
            assert 'SUCCESS1' == service.func_1()

        if expected:
            with pytest.raises(HTTPError) as error:
                service.func_2()
            assert error.type is HTTPError
            assert error.value.code == 401
            assert error.value._message.startswith(self.AUTH_BY_SESSION_ERR)
        else:
            assert 'SUCCESS2' == service.func_2()

    @pytest.mark.parametrize('token_access,required_access,expected', TEST_REQUIRED_ACCESS)
    def test_all_api_auth_by_token(self, monkeypatch, session, api_token, token_access, required_access, expected):

        @all_api_auth(*required_access)
        class Service(object):
            def func_1(self):
                return 'SUCCESS1'

            def func_2(self):
                return 'SUCCESS2'

        service = Service()

        api_token.access = ','.join(map(str, token_access))
        session.commit()
        session.refresh(api_token)
        monkeypatch.setitem(cherrypy.request.headers, 'X-Auth-Token', api_token.token)

        if expected:
            with pytest.raises(HTTPError) as error:
                service.func_1()
            assert error.type is HTTPError
            assert error.value.code == 403
            assert error.value._message.startswith(self.AUTH_BY_TOKEN_ERR)
        else:
            assert 'SUCCESS1' == service.func_1()

        if expected:
            with pytest.raises(HTTPError) as error:
                service.func_2()
            assert error.type is HTTPError
            assert error.value.code == 403
            assert error.value._message.startswith(self.AUTH_BY_TOKEN_ERR)
        else:
            assert 'SUCCESS2' == service.func_2()
