"""Identity: the global User and its authentication factors.

A ``User`` is **global, not per-organization** (ADR-002/ADR-003). One person,
one account, many organizations - an accountant serving twelve clients should
not maintain twelve passwords.

The consequences are accepted deliberately:

- ``User`` has no ``organization_id``, so RLS cannot protect it. It is treated
  as control-plane-adjacent: access goes through ``organizations`` selectors
  that always join through Membership, and product code never queries it (F-6).
- Email uniqueness is global, which enables enumeration attempts. Login,
  reset and invitation flows return uniform responses and uniform timing.
- Disabling a user affects every organization they belong to. That is
  intended: a compromised account must be stoppable everywhere at once.
  Per-organization deactivation is ``Membership.status``.

**No organization membership fields live on this model.** Identity and
membership are separate concepts; merging them is what makes multi-org
impossible later.
"""

from __future__ import annotations

import uuid
from typing import Any

from django.contrib.auth.models import AbstractBaseUser, BaseUserManager
from django.contrib.sessions.base_session import AbstractBaseSession
from django.db import models
from django.utils import timezone

from workeasy360.contracts.enums import UserStatus
from workeasy360.platform.identity.email import normalize_email


class UserManager(BaseUserManager):
    """Creates users with normalized email as the identifier."""

    use_in_migrations = True

    def _create_user(self, email: str, password: str | None, **extra: Any) -> User:
        if not email:
            raise ValueError("A user requires an email address.")
        # Capture the address as typed *before* normalising. Setting
        # email_original afterwards would store the normalised form and lose
        # the display casing the field exists to preserve.
        extra.setdefault("email_original", email)
        # Normalisation happens here *and* in Model.clean()/save() so that
        # manager, admin and direct instantiation all converge on one form.
        email = normalize_email(email)
        user = self.model(email=email, **extra)
        if password:
            user.set_password(password)
        else:
            # Unusable rather than blank: an SSO-only or invited user must not
            # be authenticable with an empty password.
            user.set_unusable_password()
        user.full_clean(exclude=["password"])
        user.save(using=self._db)
        return user

    def create_user(self, email: str, password: str | None = None, **extra: Any) -> User:
        extra.setdefault("is_staff", False)
        extra.setdefault("is_superuser", False)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email: str, password: str, **extra: Any) -> User:
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("status", UserStatus.ACTIVE.value)
        extra.setdefault("email_verified_at", timezone.now())
        if not extra["is_staff"] or not extra["is_superuser"]:
            raise ValueError("A superuser must have is_staff and is_superuser set.")
        return self._create_user(email, password, **extra)

    def get_by_natural_key(self, username: str | None) -> User:
        """Case-insensitive lookup on the normalized form.

        Without normalising here, a user who registered as ``Alice@x.com``
        could not log in as ``alice@x.com``.
        """
        return self.get(email=normalize_email(username or ""))

    def active(self) -> models.QuerySet[User]:
        return self.filter(status=UserStatus.ACTIVE.value)


class User(AbstractBaseUser):
    """A global login identity.

    ``AbstractBaseUser`` rather than ``AbstractUser``: the latter brings a
    ``username`` field we do not want, Django's model-level permissions
    (which attach to the user, not to a membership - the wrong shape for
    multi-org), and group membership that would compete with our own RBAC.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # citext would remove the need for normalisation, but it is an extension
    # and a superuser-only CREATE EXTENSION. Storing the normalized form in a
    # plain unique column achieves the same guarantee without that dependency.
    email = models.EmailField(
        max_length=254,
        unique=True,
        help_text="Normalized (lowercased, trimmed). The login identifier.",
    )
    #: Preserved for display and for addressing mail, because some providers
    #: do treat the local part as case-sensitive even though almost none do.
    email_original = models.EmailField(max_length=254, blank=True, default="")
    email_verified_at = models.DateTimeField(null=True, blank=True)

    full_name = models.CharField(max_length=255, blank=True, default="")

    status = models.CharField(
        max_length=16,
        choices=[(s.value, s.name.title()) for s in UserStatus],
        default=UserStatus.ACTIVE.value,
        db_index=True,
    )

    #: Workeasy360 staff. Distinct from any customer role: staff hold **no
    #: default access to tenant business data** and must use break-glass
    #: (04-identity-authz.md section 5).
    is_staff = models.BooleanField(
        default=False,
        help_text="Workeasy360 internal staff. Grants no tenant data access.",
    )
    is_superuser = models.BooleanField(
        default=False,
        help_text="Platform super admin. Control plane only.",
    )

    class AcquisitionSource(models.TextChoices):
        """How the account came to exist (Phase 9). Empty on accounts created
        before it was recorded. Descriptive only - nothing authorizes on it."""

        SELF_SIGNUP = "self_signup", "Self-service signup"
        INVITATION = "invitation", "Organization invitation"
        PARTNER = "partner", "Partner provisioning"
        OPERATOR = "operator", "Platform operator"

    acquisition_source = models.CharField(
        max_length=16, choices=AcquisitionSource.choices, blank=True, default=""
    )

    last_login_at = models.DateTimeField(null=True, blank=True)
    #: Counted for throttling. Lockout is time-boxed with jitter rather than
    #: permanent, so an attacker cannot lock a victim out at will.
    failed_login_count = models.PositiveIntegerField(default=0)
    locked_until = models.DateTimeField(null=True, blank=True)
    #: Consecutive failed MFA codes, and the step-up lock they have earned
    #: (``identity.services.verify_step_up_code``). Separate from the login
    #: counter above: only someone already holding the password can move
    #: these, and they must not lock the password out with them.
    mfa_failed_attempts = models.PositiveIntegerField(default=0)
    mfa_locked_until = models.DateTimeField(null=True, blank=True)

    #: Any password change, credential reset or forced logout bumps this.
    #: Sessions carry the value they were issued under and are invalidated
    #: when it moves, so revocation does not depend on enumerating sessions.
    session_epoch = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS: list[str] = []

    objects = UserManager()

    class Meta:
        db_table = "identity_user"
        indexes = [
            models.Index(fields=["status"], name="ix_user_status"),
            models.Index(fields=["email"], name="ix_user_email"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return self.email

    def clean(self) -> None:
        super().clean()
        if self.email:
            if not self.email_original:
                self.email_original = self.email
            self.email = normalize_email(self.email)

    def save(self, *args: Any, **kwargs: Any) -> None:
        # Normalising in save() as well as clean() closes the path where code
        # calls save() directly and bypasses validation, which would allow two
        # rows differing only by case.
        if self.email:
            if not self.email_original:
                self.email_original = self.email
            self.email = normalize_email(self.email)
        super().save(*args, **kwargs)

    # --- authentication predicates ----------------------------------------

    @property
    def is_active(self) -> bool:
        """Django's contract for "may authenticate", and whether a live
        session still resolves to this user.

        Derived from ``status`` rather than stored separately: two sources of
        truth for whether an account works would eventually disagree, and the
        disagreement would be a security bug.

        **Not** the failed-password lockout (Phase 12, G-16). That lockout is
        a brake on password guessing, and ``identity.services.authenticate``
        applies it before any password is compared. Folded in here, it also
        made Django treat every *live* session of the account as anonymous -
        so anyone who knew an address could sign its owner (a Super Admin
        included) out of everything, for fifteen minutes at a time, with ten
        wrong passwords. An administrator's LOCKED or DISABLED status still
        ends every session: that is a decision about the account, not a guess.
        """
        return UserStatus(self.status).can_authenticate

    @property
    def is_locked_out(self) -> bool:
        return bool(self.locked_until and self.locked_until > timezone.now())

    @property
    def is_email_verified(self) -> bool:
        return self.email_verified_at is not None

    @property
    def has_mfa(self) -> bool:
        return self.auth_factors.filter(confirmed_at__isnull=False).exists()

    def revoke_sessions(self) -> None:
        """Invalidate every session by bumping the epoch.

        Immediate revocation is the dominant operational requirement for a
        business platform - a fired employee, a compromised laptop - and it is
        why sessions are server-side rather than stateless JWTs
        (04-identity-authz.md section 3.2).
        """
        self.session_epoch += 1
        self.save(update_fields=["session_epoch", "updated_at"])


class UserSession(AbstractBaseSession):
    """A browser session, as a row that knows whose it is (Phase 8 extension).

    Django's own ``django_session`` table holds an opaque blob per key; nothing
    in it says which user a session belongs to without decoding every row. That
    made two things impossible: showing a person where they are signed in, and
    ending one of those sessions without ending all of them
    (``User.session_epoch`` remains the everywhere-at-once switch).

    ``platform.identity.sessions.SessionStore`` maintains the extra columns from
    the session's own data on every save, so they cost no additional query.
    Deleting a row *is* revocation: the next request carrying that cookie finds
    nothing and is anonymous.

    **The session key never leaves this table.** It is the bearer secret in the
    cookie; APIs identify a session by ``public_id``.
    """

    public_id = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    user = models.ForeignKey(
        User,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="browser_sessions",
    )
    #: The epoch the session was issued under (``User.session_epoch``). A row
    #: whose epoch is behind the user's is already dead - the middleware
    #: flushes it on its next request - so listings exclude it.
    session_epoch = models.PositiveIntegerField(default=0)
    #: When this key was minted. Login rotates the key, so this is the moment
    #: of sign-in for an authenticated session - and what the absolute
    #: lifetime (``SESSION_ABSOLUTE_TIMEOUT``) is measured from.
    created_at = models.DateTimeField(default=timezone.now)
    last_activity_at = models.DateTimeField(default=timezone.now)
    #: Coarse by design - see ``platform.identity.devices``.
    ip_prefix = models.CharField(max_length=64, blank=True, default="")
    device_label = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "identity_session"
        indexes = [
            models.Index(fields=["user", "-last_activity_at"], name="ix_session_user"),
        ]

    @classmethod
    def get_session_store_class(cls):  # type: ignore[no-untyped-def]
        from workeasy360.platform.identity.sessions import SessionStore

        return SessionStore


class AccountEvent(models.Model):
    """One entry in a person's own account-security trail.

    Sign-ins (and failed attempts against a real account), password changes
    and session revocations. Global, like ``User``: signing in happens before
    any organization is chosen. What an *organization* sees is
    ``organizations.MemberSignIn``, which records entry into that organization
    only - an administrator never learns how a member uses other tenants.

    Coarse location only (``platform.identity.devices``). Pruned after
    ``ACCOUNT_EVENT_RETENTION_DAYS``.
    """

    class Kind(models.TextChoices):
        SIGN_IN = "sign_in", "Signed in"
        SIGN_IN_FAILED = "sign_in_failed", "Sign-in failed"
        PASSWORD_CHANGED = "password_changed", "Password changed"
        SESSION_REVOKED = "session_revoked", "Session signed out"
        OTHER_SESSIONS_REVOKED = "other_sessions_revoked", "Other sessions signed out"
        SIGNED_OUT_EVERYWHERE = "signed_out_everywhere", "Signed out everywhere"
        ACCOUNT_DISABLED = "account_disabled", "Account disabled"
        ACCOUNT_ENABLED = "account_enabled", "Account enabled"
        ACCOUNT_UNLOCKED = "account_unlocked", "Account unlocked"
        EMAIL_VERIFIED = "email_verified", "Email address verified"
        PASSWORD_RESET = "password_reset", "Password reset by email"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    occurred_at = models.DateTimeField(default=timezone.now)
    kind = models.CharField(max_length=32, choices=Kind.choices)
    #: For a failed sign-in: ``invalid_credentials`` | ``account_locked`` |
    #: ``account_disabled``. For staff actions: who acted is in the
    #: control-plane audit trail, not here.
    reason = models.CharField(max_length=32, blank=True, default="")
    ip_prefix = models.CharField(max_length=64, blank=True, default="")
    device_label = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        db_table = "identity_account_event"
        indexes = [
            models.Index(fields=["user", "-occurred_at"], name="ix_account_event_user"),
            models.Index(fields=["occurred_at"], name="ix_account_event_time"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.kind} for {self.user_id}"


class AccountToken(models.Model):
    """A one-time link that proves possession of an email address (Phase 9):
    verifying a new account's address, or resetting a forgotten password.

    **Only the SHA-256 of the token is stored**, so a database dump yields no
    working link - the same rule as ``organizations.Invitation``. The link
    itself travels in a notification's sealed context and sits in the URL
    fragment, which browsers never send to a server.

    Single use (``used_at``) and short-lived (``expires_at``). Issuing a new
    token of a purpose retires the account's earlier unused ones of that
    purpose, so only the latest emailed link works.

    ``organization_name`` carries the one thing a signup must remember until
    the address is verified: the company the person asked to create. Nothing
    else about the request is kept here.
    """

    class Purpose(models.TextChoices):
        VERIFY_EMAIL = "verify_email", "Verify email address"
        RESET_PASSWORD = "reset_password", "Reset password"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="+")
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    token_hash = models.CharField(max_length=64, unique=True)
    organization_name = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "identity_account_token"
        indexes = [
            models.Index(fields=["user", "purpose"], name="ix_account_token_user"),
            models.Index(fields=["expires_at"], name="ix_account_token_expiry"),
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.purpose} for {self.user_id}"


class AuthFactor(models.Model):
    """A second authentication factor.

    Modelled from day one even though enforcement policy arrives later:
    retrofitting a factor model into a live authentication system is expensive
    and risky (04-identity-authz.md section 3.3).

    WebAuthn is first-class rather than an afterthought - ``credential_id``,
    ``public_key`` and ``sign_count`` are present now, so adding passkeys is a
    feature rather than a migration of the auth model.
    """

    class Kind(models.TextChoices):
        TOTP = "totp", "Authenticator app"
        WEBAUTHN = "webauthn", "Security key or passkey"
        RECOVERY_CODE = "recovery_code", "Recovery code"
        SMS = "sms", "SMS"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="auth_factors")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    label = models.CharField(max_length=100, blank=True, default="")

    #: A KMS reference, never the raw secret. Field-level encryption applies
    #: to MFA secrets (12-security.md section 6).
    secret_ref = models.CharField(max_length=255, blank=True, default="")

    credential_id = models.BinaryField(null=True, blank=True)
    public_key = models.BinaryField(null=True, blank=True)
    #: A counter that may only move forward. WebAuthn: the authenticator's
    #: signature counter (going backwards means the credential was copied).
    #: TOTP: the last accepted time step - RFC 6238 section 5.2 replay
    #: protection, so an accepted code can never be accepted again.
    sign_count = models.BigIntegerField(default=0)

    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "identity_auth_factor"
        indexes = [
            models.Index(fields=["user", "kind"], name="ix_factor_user_kind"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "kind", "label"], name="uq_factor_user_kind_label"
            )
        ]

    def __str__(self) -> str:  # pragma: no cover - display only
        return f"{self.get_kind_display()} for {self.user_id}"

    @property
    def satisfies_aal2(self) -> bool:
        """Whether this factor can raise the session to AAL 2.

        SMS is supported but classified as weak and cannot satisfy AAL 2 for
        staff or partner admins (04-identity-authz.md section 3.3).
        """
        return self.confirmed_at is not None and self.kind in {
            self.Kind.TOTP,
            self.Kind.WEBAUTHN,
        }
