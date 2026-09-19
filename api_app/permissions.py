from rest_framework.permissions import SAFE_METHODS, BasePermission


class IsOwner(BasePermission):
    """
    Allows access only to authenticated users who own the object.

    Supported ownership patterns:
    - obj.owner
    - obj.project.owner
    """

    def has_permission(self, request, view):
        return bool(
            request.user
            and request.user.is_authenticated
        )

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False

        owner = getattr(obj, "owner", None)

        if owner is None:
            project = getattr(obj, "project", None)
            owner = getattr(project, "owner", None)

        return owner == request.user


class IsOwnerOrReadOnly(IsOwner):
    """
    Authenticated owners may access their objects.

    Safe methods are read-only, but ownership is still required.
    """

    def has_object_permission(self, request, view, obj):
        if request.method in SAFE_METHODS:
            return super().has_object_permission(
                request,
                view,
                obj,
            )

        return super().has_object_permission(
            request,
            view,
            obj,
        )