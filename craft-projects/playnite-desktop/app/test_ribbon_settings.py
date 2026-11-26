from ribbon_settings import RibbonArtworkSettings


class Store:
    def __init__(self):
        self.records = {}
        self.calls = []

    def load(self, provider):
        self.calls.append(('load', provider))
        return self.records.get(provider)

    def save(self, provider, record):
        self.calls.append(('save', provider))
        self.records[provider] = record

    def clear(self, provider):
        self.calls.append(('clear', provider))
        self.records.pop(provider, None)


def test_key_lookup_is_lazy_and_credential_is_not_exposed():
    store = Store()
    settings = RibbonArtworkSettings(store)
    assert store.calls == []
    assert not settings.keyConfigured
    assert not settings.checkKeyring()
    assert store.calls == [('load', 'steamgriddb')]
    assert settings.saveApiKey('fixture-private-api-key')
    assert settings.keyConfigured
    assert 'fixture-private-api-key' not in settings.message
    assert settings.clearApiKey()
    assert not settings.keyConfigured


def test_locked_keyring_is_reported_without_exposing_provider_error():
    class Locked:
        def load(self, _provider): raise RuntimeError('fixture-secret-error')
        def save(self, *_args): raise RuntimeError('fixture-secret-error')
        def clear(self, *_args): raise RuntimeError('fixture-secret-error')
    settings = RibbonArtworkSettings(Locked())
    assert not settings.checkKeyring()
    assert 'unavailable' in settings.message.lower()
    assert 'fixture-secret-error' not in settings.message
