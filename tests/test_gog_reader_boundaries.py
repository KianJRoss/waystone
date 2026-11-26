from pathlib import Path
import pytest
from native_services.gog_local_receipt import verify_selected_gog_directory

@pytest.mark.parametrize('value',[None,[],{},123,'relative','/tmp/invalid\x00path'])
def test_invalid_destination_returns_safe_failure(value):
    assert verify_selected_gog_directory('123',value)==(None,'invalid_destination')

def test_no_metadata_means_no_state_or_scan(tmp_path):
    assert verify_selected_gog_directory('123',tmp_path)==(None,'local_metadata_missing')
    assert list(tmp_path.iterdir())==[]
