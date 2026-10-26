from rest_framework.permissions import BasePermission, SAFE_METHODS


class PosRolePermission(BasePermission):
    """Require an authenticated POS user with an appropriate Django group."""

    read_roles = {"admin", "manager", "cashier"}
    write_roles = {"admin", "manager"}

    def has_permission(self, request, view):
        user = request.user
        if not user or not user.is_authenticated:
            return False
        if user.is_superuser:
            return True
        allowed = self.read_roles if request.method in SAFE_METHODS else self.write_roles
        return user.groups.filter(name__in=allowed).exists()


class PosSalesPermission(PosRolePermission):
    read_roles = {"admin", "manager", "cashier"}
    write_roles = {"admin", "manager", "cashier"}


class PosManagerPermission(PosRolePermission):
    read_roles = {"admin", "manager", "cashier"}
    write_roles = {"admin", "manager"}


class PosCashierPermission(PosRolePermission):
    read_roles = {"admin", "manager", "cashier"}
    write_roles = {"admin", "manager", "cashier"}
