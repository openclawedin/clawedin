class UserRouter:
    route_app_labels = {"identity"}
    route_model_names = {"user"}
    users_db = "users"

    def _is_user_model(self, model):
        return (
            model is not None
            and model._meta.app_label in self.route_app_labels
            and model._meta.model_name in self.route_model_names
        )

    def db_for_read(self, model, **hints):
        if self._is_user_model(model):
            return self.users_db
        return None

    def db_for_write(self, model, **hints):
        if self._is_user_model(model):
            return self.users_db
        return None

    def allow_relation(self, obj1, obj2, **hints):
        if self._is_user_model(obj1._meta.model) or self._is_user_model(obj2._meta.model):
            return True
        return None

    def allow_migrate(self, db, app_label, model_name=None, **hints):
        hinted_model = hints.get("model")
        if model_name is None and hinted_model is not None:
            model_name = hinted_model._meta.model_name

        if app_label in self.route_app_labels and model_name in self.route_model_names:
            return db == self.users_db
        return None
