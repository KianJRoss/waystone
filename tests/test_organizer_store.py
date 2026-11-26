

def test_register_ids_adds_new_games_and_keeps_existing(tmp_path):
    from native_services.organizer_store import OrganizerStore
    store = OrganizerStore.create(tmp_path / 'organizer.sqlite3', ['a', 'b'])
    assert store.register_ids(['a', 'b', 'c']) == 1
    assert store.register_ids(['c']) == 0          # nothing new, and 'a'/'b' are never dropped
    assert store.canonical_ids() == ('a', 'b', 'c')
