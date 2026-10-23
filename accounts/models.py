from django.contrib.auth.models import AbstractUser, UserManager
from django.db import models
from django.db.models.functions import Lower


class Role(models.TextChoices):
    SELLER = "seller", "Leiloeiro"
    BIDDER = "bidder", "Licitante"


class EmailUserManager(UserManager):
    def _create_user(self, email, password, **extra):
        user = self.model(email=self.normalize_email(email).lower(), **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_user(self, email, password=None, **extra):
        extra.setdefault("role", Role.BIDDER)
        return self._create_user(email, password, **extra)

    def create_superuser(self, email, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        extra.setdefault("role", Role.SELLER)
        return self._create_user(email, password, **extra)


class User(AbstractUser):
    username = None
    # unique=True é exigido pelo Django para o USERNAME_FIELD (auth.E003); a constraint
    # abaixo acrescenta a regra sem diferenciar maiúsculas.
    email = models.EmailField(unique=True)
    role = models.CharField(max_length=10, choices=Role.choices)

    USERNAME_FIELD = "email"
    REQUIRED_FIELDS = []

    objects = EmailUserManager()

    class Meta:
        constraints = [
            # Impede "A@x.com" e "a@x.com" como contas distintas.
            models.UniqueConstraint(Lower("email"), name="unique_user_email_ci"),
        ]
