from django.contrib.auth.base_user import BaseUserManager


class UserManager(BaseUserManager):
    use_in_migrations = True

    def _create(self, phone, password=None, **extra):
        if not phone:
            raise ValueError('Телефон обязателен')
        user = self.model(phone=phone, **extra)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_user(self, phone, password=None, **extra):
        extra.setdefault('role', 'USER')
        extra.setdefault('is_staff', False)
        extra.setdefault('is_superuser', False)
        user = self._create(phone, password, **extra)
        user.ensure_identity()
        return user

    def create_director(self, phone, password, **extra):
        extra['role'] = 'DIRECTOR'
        extra.setdefault('is_registration_complete', True)
        return self._create(phone, password, **extra)

    def create_superuser(self, phone, password=None, **extra):
        extra.setdefault('role', 'DIRECTOR')
        extra.setdefault('is_staff', True)
        extra.setdefault('is_superuser', True)
        extra.setdefault('is_registration_complete', True)
        if extra.get('is_staff') is not True:
            raise ValueError('Суперпользователь должен иметь is_staff=True')
        if extra.get('is_superuser') is not True:
            raise ValueError('Суперпользователь должен иметь is_superuser=True')
        return self._create(phone, password, **extra)

    def clients(self):
        return self.filter(role='USER')
