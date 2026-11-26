"""Optional add-on test collector.

When `private_addons` is present, this module re-exports the add-on test suites
so the standard `pytest tests craft-projects/playnite-desktop/app` command runs
them automatically. When `private_addons` is omitted in the public export, this
module cleanly collects zero add-on tests.
"""
from __future__ import annotations

try:
    from private_addons.tests.test_emulation_labels import EmulationLabelsTests
    from private_addons.tests.test_native_provider_ui_independent import IndependentNativeProviderUITests
    from private_addons.tests.test_native_provider_view import NativeDialogTests, NativeProviderTests
    from private_addons.tests.test_private_addons import PrivateAddonsTests
except ImportError:
    pass
