"""The six CityLand 9 roles.

The values are the strings already stored in the `user.role` column, so existing
accounts keep working. They are the members of the MySQL ENUM in database/schema.sql.
Code may refer to a role by its value ("super_admin") or by its name ("SUPERADMIN").
"""

SUPER_ADMIN = "super_admin"
ADMIN = "admin"
MANAGER = "manager"
STAFF = "staff"
ACCOUNTING = "accounting"
RESIDENT = "resident"

ALL_ROLES = (SUPER_ADMIN, ADMIN, MANAGER, STAFF, ACCOUNTING, RESIDENT)

ROLE_LABELS = {
    SUPER_ADMIN: "Superadmin",
    ADMIN: "Admin",
    MANAGER: "Manager (HR & Payroll)",
    STAFF: "Staff",
    ACCOUNTING: "Accounting",
    RESIDENT: "Resident",
}

# React portal (URL prefix + page folder) for each role.
PORTALS = {
    SUPER_ADMIN: "superadmin",
    ADMIN: "admin",
    MANAGER: "hr",
    STAFF: "staff",
    ACCOUNTING: "accounting",
    RESIDENT: "resident",
}

_ALIASES = {
    "SUPERADMIN": SUPER_ADMIN, "SUPER_ADMIN": SUPER_ADMIN, "ADMIN": ADMIN, "MANAGER": MANAGER,
    "HR": MANAGER, "STAFF": STAFF, "ACCOUNTING": ACCOUNTING, "RESIDENT": RESIDENT,
}


def normalize_role(role):
    """'SUPERADMIN' / 'super_admin' -> 'super_admin'. Raises ValueError for unknown roles."""
    value = str(role or "").strip()
    if value in ALL_ROLES:
        return value
    if value.upper() in _ALIASES:
        return _ALIASES[value.upper()]
    raise ValueError(f"Unknown role: {role!r}")


def is_valid_role(role):
    return str(role or "") in ALL_ROLES
