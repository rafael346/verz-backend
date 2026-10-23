from rest_framework.permissions import BasePermission

from .models import Role


class _HasRole(BasePermission):
    role = None

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and user.role == self.role)


class IsSeller(_HasRole):
    role = Role.SELLER


class IsBidder(_HasRole):
    role = Role.BIDDER
