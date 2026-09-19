from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsOwner(BasePermission):
    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        owner = getattr(obj, "owner", None)
        if owner is None and hasattr(obj, "project"):
            owner = obj.project.owner
        return owner == request.user

    def has_permission(self, request, view):
        return bool(request.user and request.user.is_authenticated)


class IsOwnerOrReadOnly(IsOwner):
    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return super().has_object_permission(request, view, obj)
        return super().has_object_permission(request, view, obj)
