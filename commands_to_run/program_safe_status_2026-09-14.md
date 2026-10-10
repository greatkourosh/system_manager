# Program SAFE cleanup — on-disk status (2026-09-14)

Proposal: 90 SAFE groups, 144 Windows delete-paths, 3.43 GB nominal.

## Real, actionable deletes (4 files, 0.09 GB)

- group sha 9204011a · keep `E:\Programs\Network\VPN\V2RayN\6.43\v2rayN-With-Core\bin\v2fly_v5\v2ray.exe` (intact=False)
  - DELETE `E:\Programs\Network\VPN\V2RayN\7.7.1\v2rayN-windows-64\bin\v2fly_v5\v2ray.exe` → `/media/kourosh/Kourosh/Programs/Network/VPN/V2RayN/7.7.1/v2rayN-windows-64/bin/v2fly_v5/v2ray.exe` (32831488 bytes)
- group sha e1269f0f · keep `E:\Programs\Network\VPN\V2RayN\6.43\v2rayN-With-Core\bin\hysteria\hysteria-windows-amd64.exe` (intact=False)
  - DELETE `E:\Programs\Network\VPN\V2RayN\7.7.1\v2rayN-windows-64\bin\hysteria\hysteria-windows-amd64.exe` → `/media/kourosh/Kourosh/Programs/Network/VPN/V2RayN/7.7.1/v2rayN-windows-64/bin/hysteria/hysteria-windows-amd64.exe` (19602944 bytes)
- group sha 54576df1 · keep `E:\Programs\Network\VPN\V2RayN\6.43\v2rayN-With-Core\bin\hysteria2\hysteria-windows-amd64.exe` (intact=False)
  - DELETE `E:\Programs\Network\VPN\V2RayN\7.7.1\v2rayN-windows-64\bin\hysteria2\hysteria-windows-amd64.exe` → `/media/kourosh/Kourosh/Programs/Network/VPN/V2RayN/7.7.1/v2rayN-windows-64/bin/hysteria2/hysteria-windows-amd64.exe` (15119872 bytes)
- group sha 529e96ab · keep `E:\Programs\Network\VPN\V2RayN\7.7.1\v2rayN-windows-64\bin\mihomo\mihomo-windows-amd64-compatible.exe` (intact=False)
  - DELETE `E:\Programs\Network\VPN\V2RayN\7.12.5\v2rayN-windows-64\bin\mihomo\mihomo-windows-amd64-compatible.exe` → `/media/kourosh/Kourosh/Programs/Network/VPN/V2RayN/7.12.5/v2rayN-windows-64/bin/mihomo/mihomo-windows-amd64-compatible.exe` (29474304 bytes)

## Stale — proposal is obsolete

The 2026-09-01 scan data is 13 days old and the filesystem has reorganized since:

- **4 actionable deletes**: keep copies are ALSO missing (V2RayN folder layout changed; versions reorganized).
  Cannot safely execute — the "keep" originals no longer live where the proposal says; deleting based on
  the stale path would risk removing the only surviving copy.
- **134 stale paths**: files already moved/deleted (VPN-FTP mirror dirs now empty; Canon `acrocef_1` exes
  removed by Acrobat updater; etc.).
- **6 unmapped**: H:\projects\..., C:\Users\... not mounted in this Docker/Linux context.

## Recommendation

Re-run the scanner + cleanup_proposal.py to refresh `cleanup_proposal.json` against the current tree,
then re-evaluate. The stale 2026-09-01 proposal should NOT be executed.