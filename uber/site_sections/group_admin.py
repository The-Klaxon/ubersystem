import cherrypy

from datetime import date, datetime, timedelta
from pockets import readable_join
from pytz import UTC
from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload

from uber.config import c
from uber.decorators import ajax, all_renderable, csrf_protected, log_pageview, site_mappable
from uber.errors import HTTPRedirect
from uber.forms import attendee as attendee_forms, group as group_forms, load_forms
from uber.models import Attendee, Email, Event, Group, GuestGroup, GuestMerch, PageViewTracking, Tracking, SignedDocument
from uber.utils import check, convert_to_absolute_url, validate_model
from uber.payments import ReceiptManager


@all_renderable()
class Root:
    def _required_message(self, params, fields):
        missing = [s for s in fields if not params.get(s, '').strip() or params.get(s, '') == "0"]
        if missing:
            return '{} {} required field{}'.format(
                readable_join([s.replace('_', ' ').title() for s in missing]),
                'is a' if len(missing) == 1 else 'are',
                's' if len(missing) > 1 else '')
        return ''

    def index(self, session, message='', show_all=None):
        if show_all:
            groups = session.viewable_groups()
        else:
            groups = session.viewable_groups().filter(~Group.status.in_([c.DECLINED, c.IMPORTED, c.CANCELLED]))
        dealer_groups = groups.filter(Group.is_dealer == True)
        return {
            'message': message,
            'groups': groups.options(joinedload(Group.attendees), joinedload(Group.leader), joinedload(Group.active_receipt)),
            'guest_checklist_items': GuestGroup(group_type=c.GUEST).sorted_checklist_items,
            'band_checklist_items': GuestGroup(group_type=c.BAND).sorted_checklist_items,
            'num_dealer_groups': dealer_groups.count(),
            'dealer_groups':      dealer_groups.options(joinedload(Group.attendees), joinedload(Group.leader), joinedload(Group.active_receipt)),
            'dealer_badges':      sum(g.badges for g in dealer_groups),
            'tables':            sum(g.tables for g in dealer_groups),
            'show_all': show_all,
            'unapproved_tables': sum(g.tables for g in dealer_groups if g.status == c.UNAPPROVED),
            'waitlisted_tables': sum(g.tables for g in dealer_groups if g.status == c.WAITLISTED),
            'approved_tables':   sum(g.tables for g in dealer_groups if g.status == c.APPROVED)
        }

    def new_group_from_attendee(self, session, id):
        attendee = session.attendee(id)
        if attendee.group:
            if c.HAS_REGISTRATION_ACCESS:
                link = '../registration/form?id={}&'.format(attendee.id)
            else:
                link = '../accounts/homepage?'
            raise HTTPRedirect('{}message={}', link, "That attendee is already in a group!")
        group = Group(name="{}'s Group".format(attendee.full_name))
        attendee.group = group
        group.leader = attendee
        session.add(group)
        
        raise HTTPRedirect('form?id={}&message={}', group.id, "Group successfully created.")
    
    def resend_signnow_link(self, session, id):
        group = session.group(id)

        document = session.query(SignedDocument).filter_by(model="Group", fk_id=id).first()
        if not document:
            raise HTTPRedirect("form?id={}&message={}").format(id, "SignNow document not found.")
        
        document.send_dealer_signing_invite(group)
        document.last_emailed = datetime.now(UTC)
        session.add(document)
        raise HTTPRedirect("form?id={}&message={}", id, "SignNow link sent!")

    @log_pageview
    def form(self, session, new_dealer='', message='', **params):
        if params.get('id') not in [None, '', 'None']:
            group = session.group(params.get('id'))
            if cherrypy.request.method == 'POST' and params.get('id') not in [None, '', 'None']:
                receipt_items = ReceiptManager.auto_update_receipt(group, session.get_receipt_by_model(group), params)
                session.add_all(receipt_items)
        else:
            group = Group()

        if group.is_dealer or 'new_dealer' in params:
            form_list = ['AdminTableInfo', 'ContactInfo']
        else:
            form_list = ['AdminGroupInfo']

        forms = load_forms(params, group, group_forms, form_list)
        for form in forms.values():
            if hasattr(form, 'new_badge_type'):
                form['new_badge_type'].data = group.leader.badge_type if group.leader else c.ATTENDEE_BADGE
            form.populate_obj(group, is_admin=True)

        signnow_last_emailed = None
        if c.SIGNNOW_DEALER_TEMPLATE_ID and group.is_dealer and group.status == c.APPROVED:
            existing_doc = session.query(SignedDocument).filter_by(model="Group", fk_id=group.id).first()
            if not existing_doc:
                document = SignedDocument(fk_id=group.id, model="Group", ident="terms_and_conditions")
                document.send_dealer_signing_invite(group)
                document.last_emailed = datetime.now(UTC)
                session.add(document)
                existing_doc = document
            signnow_last_emailed = existing_doc.last_emailed

        group_info_form = forms.get('group_info', forms.get('table_info'))

        if cherrypy.request.method == 'POST':
            new_with_leader = any(params.get(info) for info in ['first_name', 'last_name', 'email'])
            message = message or self._required_message(params, ['name'])
            
            if not message and group.is_new and (params.get('guest_group_type') or new_dealer or group.is_dealer):
                message = self._required_message(params, ['first_name', 'last_name', 'email'])

            if not message:
                session.add(group)

                if group.is_new and params.get('guest_group_type'):
                    group.auto_recalc = False

                if group.is_new or group.badges != group_info_form.badges.data:
                    new_ribbon = params.get('ribbon', c.BAND if params.get('guest_group_type') == str(c.BAND) else None)
                    new_badge_type = params.get('new_badge_type', c.ATTENDEE_BADGE)
                    test_permissions = Attendee(badge_type=new_badge_type, ribbon=new_ribbon, paid=c.PAID_BY_GROUP)
                    new_badge_status = c.PENDING_STATUS if not session.admin_can_create_attendee(test_permissions) else c.NEW_STATUS
                    message = session.assign_badges(
                        group,
                        int(params.get('badges', 0)) or int(new_with_leader),
                        new_badge_type=new_badge_type,
                        new_ribbon_type=new_ribbon,
                        badge_status=new_badge_status,
                        )

            if not message and group.is_new and new_with_leader:
                session.commit()
                leader = group.leader = group.attendees[0]
                leader.placeholder = True
                forms = load_forms(params, leader, attendee_forms, ['PersonalInfo'])
                all_errors = validate_model(forms, leader, Attendee(**leader.to_dict()), is_admin=True)
                if all_errors:
                    session.delete(group)
                    session.commit()
                    message = " ".join(list(zip(*[all_errors]))[1])

            if not message:
                if params.get('guest_group_type'):
                    group.guest = group.guest or GuestGroup()
                    group.guest.group_type = params.get('guest_group_type')
                
                if group.is_new and group.is_dealer:
                    if group.status == c.APPROVED and group.amount_unpaid:
                        raise HTTPRedirect('../preregistration/group_members?id={}', group.id)
                    elif group.status == c.APPROVED:
                        raise HTTPRedirect(
                            'index?message={}', group.name + ' has been uploaded and approved')
                    else:
                        raise HTTPRedirect(
                            'index?message={}', group.name + ' is uploaded as ' + group.status_label)
                    
                raise HTTPRedirect('form?id={}&message={} has been saved', group.id, group.name)

        return {
            'message': message,
            'group': group,
            'forms': forms,
            'signnow_last_emailed': signnow_last_emailed,
            'guest_group_type': params.get('guest_group_type', ''),
            'badges': params.get('badges', group.badges if group else 0),
            'first_name': params.get('first_name', ''),
            'last_name': params.get('last_name', ''),
            'email': params.get('email', ''),
            'new_dealer': new_dealer,
        }
    
    @ajax
    def validate_dealer(self, session, form_list=[], **params):
        if params.get('id') in [None, '', 'None']:
            group = Group()
        else:
            group = session.group(params.get('id'))

        if not form_list:
            form_list = ['ContactInfo', 'AdminTableInfo']
        elif isinstance(form_list, str):
            form_list = [form_list]
        forms = load_forms(params, group, group_forms, form_list)

        all_errors = validate_model(forms, group, Group(**group.to_dict()), is_admin=True)
        if all_errors:
            return {"error": all_errors}

        return {"success": True}

    def history(self, session, id):
        group = session.group(id)

        if group.leader:
            emails = session.query(Email).filter(
                or_(Email.to == group.leader.email, Email.fk_id == id)).order_by(Email.when).all()
        else:
            emails = {}

        return {
            'group': group,
            'emails': emails,
            'changes': session.query(Tracking).filter(or_(
                Tracking.links.like('%group({})%'.format(id)),
                and_(Tracking.model == 'Group', Tracking.fk_id == id))).order_by(Tracking.when).all(),
            'pageviews': session.query(PageViewTracking).filter(PageViewTracking.what == "Group id={}".format(id))
        }
        
    @csrf_protected
    def delete(self, session, id, confirmed=None):
        group = session.group(id)
        if group.badges - group.unregistered_badges and not confirmed:
            raise HTTPRedirect('deletion_confirmation?id={}', id)
        else:
            for attendee in group.attendees:
                session.delete(attendee)
            session.delete(group)
            raise HTTPRedirect('index?message={}', 'Group deleted')

    def deletion_confirmation(self, session, id):
        return {'group': session.group(id)}

    @csrf_protected
    def assign_leader(self, session, group_id, attendee_id):
        group = session.group(group_id)
        attendee = session.attendee(attendee_id)
        if attendee not in group.attendees:
            raise HTTPRedirect('form?id={}&message={}', group_id, 'That attendee has been removed from the group')
        else:
            group.leader_id = attendee_id
            raise HTTPRedirect('form?id={}&message={}', group_id, 'Group leader set')
        
    def checklist_info(self, session, message='', event_id=None, **params):
        guest = session.guest_group(params)
        if not session.admin_can_see_guest_group(guest):
            raise HTTPRedirect('index?message={}', 'You cannot view {} groups'.format(guest.group_type_label.lower()))
        
        if cherrypy.request.method == 'POST':
            if event_id:
                guest.event_id = event_id
            message = check(guest)
            if not message:
                for field in ['estimated_loadin_minutes', 'estimated_performance_minutes']:
                    if field in params:
                        field_name = "load-in" if field == 'estimated_loadin_minutes' else 'performance'
                        if not params.get(field):
                            message = "Please enter more than 0 estimated {} minutes".format(field_name)
                        elif not str(params.get(field, '')).isdigit():
                            message = "Please enter a whole number for estimated {} minutes".format(field_name)
            if not message:
                raise HTTPRedirect('index?message={}{}', guest.group.name, ' data uploaded')

        events = session.query(Event).filter_by(location=c.CONCERTS).order_by(Event.start_time).all()
        return {
            'guest': guest,
            'message': message,
            'events': [(event.id, event.name) for event in events]
        }
