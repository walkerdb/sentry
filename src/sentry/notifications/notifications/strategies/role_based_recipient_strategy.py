from __future__ import annotations

from abc import ABCMeta
from collections.abc import MutableMapping
from typing import TYPE_CHECKING

from django.db.models import QuerySet

from sentry import roles
from sentry.models.organizationmember import OrganizationMember
from sentry.roles.manager import OrganizationRole
from sentry.services.hybrid_cloud.user import RpcUser
from sentry.services.hybrid_cloud.user.service import user_service
from sentry.types.actor import Actor, ActorType

if TYPE_CHECKING:
    from sentry.models.organization import Organization


class RoleBasedRecipientStrategy(metaclass=ABCMeta):
    member_by_user_id: MutableMapping[int, OrganizationMember] = {}
    role: OrganizationRole | None = None
    scope: str | None = None

    def __init__(self, organization: Organization):
        self.organization = organization

    def get_member(self, user: RpcUser | Actor) -> OrganizationMember:
        # cache the result
        actor = Actor.from_object(user)
        if actor.actor_type != ActorType.USER:
            raise OrganizationMember.DoesNotExist()
        user_id = actor.id
        if user_id not in self.member_by_user_id:
            self.member_by_user_id[user_id] = OrganizationMember.objects.get(
                user_id=user_id, organization=self.organization
            )
        return self.member_by_user_id[user_id]

    def set_member_in_cache(self, member: OrganizationMember) -> None:
        """
        A way to set a member in a cache to avoid a query.
        """
        if member.user_id is not None:
            self.member_by_user_id[member.user_id] = member

    def determine_recipients(
        self,
    ) -> list[RpcUser]:
        members = self.determine_member_recipients()
        # store the members in our cache
        for member in members:
            self.set_member_in_cache(member)
        # convert members to users
        return user_service.get_many_by_id(
            ids=[member.user_id for member in members if member.user_id]
        )

    def determine_member_recipients(self) -> QuerySet[OrganizationMember]:
        """
        Depending on the type of request this might be all organization owners,
        a specific person, or something in between.
        """
        # default strategy is OrgMembersRecipientStrategy
        members = OrganizationMember.objects.get_contactable_members_for_org(self.organization.id)

        if not self.scope and not self.role:
            return members

        # you can either set the scope or the role for now
        # if both are set we use the scope
        valid_roles = []
        if self.role and not self.scope:
            valid_roles = [self.role.id]
        elif self.scope:
            valid_roles = [r.id for r in roles.get_all() if r.has_scope(self.scope)]

        members = members.filter(role__in=valid_roles)

        return members

    def build_notification_footer_from_settings_url(self, settings_url: str) -> str:
        if self.scope and not self.role:
            return (
                "You are receiving this notification because you have the scope "
                f"{self.scope} | {settings_url}"
            )

        role_name = "Member"
        if self.role:
            role_name = self.role.name

        return (
            "You are receiving this notification because you're listed as an organization "
            f"{role_name} | {settings_url}"
        )
