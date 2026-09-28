from casefile_ai.domain.ids import derived_id, new_id


def test_root_ids_are_unique() -> None:
    assert new_id() != new_id()


def test_derived_ids_are_stable_for_identical_versioned_identity() -> None:
    first = derived_id("page", "document-version-1", 1)
    second = derived_id("page", "document-version-1", 1)

    assert first == second


def test_derived_ids_change_when_versioned_identity_changes() -> None:
    old = derived_id("chunk", "page-1", "chunker-v1", 0, 100, "hash-a")
    changed = derived_id("chunk", "page-1", "chunker-v2", 0, 100, "hash-a")

    assert old != changed
