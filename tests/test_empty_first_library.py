"""A brand-new user with no launchers starts with an empty library; stores accept that and grow later (W22 clean
install test found Waystone refusing to start)."""
from native_services.catalog_link_store import CatalogLinkStore
from native_services.organizer_store import OrganizerStore


def test_organizer_and_link_stores_start_empty_then_grow(tmp_path):
    organizer = OrganizerStore.create(tmp_path / "organizer.sqlite3", [])
    links = CatalogLinkStore.create(tmp_path / "links.sqlite3", [])
    assert tuple(organizer.canonical_ids()) == () and tuple(links.canonical_ids()) == ()
    organizer.register_ids(["steam:1", "epic:Sugar"])
    links.register_ids(["steam:1", "epic:Sugar"])
    assert set(organizer.canonical_ids()) == {"steam:1", "epic:Sugar"}
    assert set(links.canonical_ids()) == {"steam:1", "epic:Sugar"}
