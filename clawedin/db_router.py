from django.conf import settings


class UserDomainRouter:
    def __init__(self):
        self.user_domain_alias = settings.USER_DOMAIN_DB_ALIAS
        self.user_domain_apps = frozenset(settings.USER_DOMAIN_DB_APPS)

    def _is_user_domain_model(self, model) -> bool:
        return model._meta.app_label in self.user_domain_apps

    def db_for_read(self, model, **hints):
        if self._is_user_domain_model(model):
            return self.user_domain_alias
        return None

    def db_for_write(self, model, **hints):
        if self._is_user_domain_model(model):
            return self.user_domain_alias
        return None

    def allow_relation(self, obj1, obj2, **hints):
        obj1_in_domain = self._is_user_domain_model(obj1.__class__)
        obj2_in_domain = self._is_user_domain_model(obj2.__class__)
        if obj1_in_domain == obj2_in_domain:
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        if app_label in self.user_domain_apps:
            return db == self.user_domain_alias
        if db == self.user_domain_alias:
            return False
        return None
