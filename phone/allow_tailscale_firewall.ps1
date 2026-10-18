# Run once, as Administrator (right-click PowerShell > Run as administrator):
#   powershell -ExecutionPolicy Bypass -File phone\allow_tailscale_firewall.ps1
#
# Lets Windows accept connections to Adoption Finder (TCP 8000) ONLY from Tailscale addresses
# (100.64.0.0/10), so other devices on your Wi-Fi can't even reach it. The app's token is a second lock.
#
# If Windows shows a "Allow Python to communicate on networks?" prompt the first time the app runs with
# phone access on, choose Cancel. That prompt would open the port to your whole Wi-Fi instead.

$name = "Adoption Finder (Tailscale only)"
$port = 8000
if (Get-NetFirewallRule -DisplayName $name -ErrorAction SilentlyContinue) {
    Remove-NetFirewallRule -DisplayName $name
}
New-NetFirewallRule -DisplayName $name -Direction Inbound -Protocol TCP -LocalPort $port `
    -RemoteAddress 100.64.0.0/10 -Action Allow -Profile Any | Out-Null
Write-Host "Done: TCP $port is open to Tailscale addresses only."
Write-Host "To remove it later: Remove-NetFirewallRule -DisplayName '$name'"
