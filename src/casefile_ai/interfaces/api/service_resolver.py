"""Deferred service lookup keeps resources inside the application lifespan."""

from collections.abc import Callable


def service_resolver[Service](
    service: Service | None,
    factory: Callable[[], Service] | None,
) -> Callable[[], Service]:
    if service is not None:
        return lambda: service
    if factory is not None:
        return factory
    raise ValueError("a service or lifespan service factory is required")
