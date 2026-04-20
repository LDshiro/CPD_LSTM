from cpdshadow import __version__
from cpdshadow.config import AppConfig
from cpdshadow.ids import make_run_id
from cpdshadow.monitoring import MonitoringAlert, MonitoringDecision
from cpdshadow.portfolio import PortfolioSizingResult, TargetPosition
from cpdshadow.storage.registry import RunRegistryRow



def test_version_exists() -> None:
    assert __version__ == "0.1.0"



def test_current_step_types_importable() -> None:
    assert AppConfig is not None
    assert MonitoringAlert is not None
    assert MonitoringDecision is not None
    assert TargetPosition is not None
    assert PortfolioSizingResult is not None
    assert make_run_id is not None
    assert RunRegistryRow is not None
