from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_kubernetes_client_installer_is_pinned_and_verifies_checksums() -> None:
    script = (ROOT / "scripts" / "install-kubernetes-tools.ps1").read_text(encoding="utf-8")

    assert 'KubectlVersion = "v1.36.4"' in script
    assert 'KubeloginVersion = "v0.2.19"' in script
    assert "kubectl.exe.sha256" in script
    assert "kubelogin-win-amd64.zip.sha256" in script
    assert "Get-FileHash" in script
    assert "az aks install-cli" not in script


def test_azure_apply_does_not_download_unpinned_clients() -> None:
    script = (ROOT / "scripts" / "azure-apply-reviewed.ps1").read_text(encoding="utf-8")

    assert "az aks install-cli" not in script
    assert "install-kubernetes-tools.ps1" in script
