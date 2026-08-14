param(
    [Parameter(Mandatory = $true)]
    [string]$ProjectId,

    [Parameter(Mandatory = $true)]
    [string]$ServiceAccountEmail,

    [Parameter(Mandatory = $true)]
    [string]$GcloudPath
)

$ErrorActionPreference = "Stop"

$passwordSecret = "readi-password-sha256"
$sessionSecret = "readi-session-secret"
$alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
$randomBytes = [byte[]]::new(12)
[System.Security.Cryptography.RandomNumberGenerator]::Fill($randomBytes)
$characters = for ($index = 0; $index -lt $randomBytes.Length; $index++) {
    $alphabet[$randomBytes[$index] % $alphabet.Length]
}
$raw = -join $characters
$password = "READi-{0}-{1}-{2}" -f $raw.Substring(0, 4), $raw.Substring(4, 4), $raw.Substring(8, 4)

$passwordBytes = [System.Text.Encoding]::UTF8.GetBytes($password)
$passwordHashBytes = [System.Security.Cryptography.SHA256]::HashData($passwordBytes)
$passwordHash = [Convert]::ToHexString($passwordHashBytes).ToLowerInvariant()

$sessionBytes = [byte[]]::new(32)
[System.Security.Cryptography.RandomNumberGenerator]::Fill($sessionBytes)
$sessionKey = [Convert]::ToHexString($sessionBytes).ToLowerInvariant()

foreach ($secretName in @($passwordSecret, $sessionSecret)) {
    & $GcloudPath secrets describe $secretName --project=$ProjectId --quiet *> $null
    if ($LASTEXITCODE -ne 0) {
        & $GcloudPath secrets create $secretName --project=$ProjectId --replication-policy=automatic --quiet
        if ($LASTEXITCODE -ne 0) {
            throw "Failed to create Secret Manager secret: $secretName"
        }
    }
}

$passwordHash | & $GcloudPath secrets versions add $passwordSecret --project=$ProjectId --data-file=- --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Failed to store the password hash."
}

$sessionKey | & $GcloudPath secrets versions add $sessionSecret --project=$ProjectId --data-file=- --quiet
if ($LASTEXITCODE -ne 0) {
    throw "Failed to store the session key."
}

foreach ($secretName in @($passwordSecret, $sessionSecret)) {
    & $GcloudPath secrets add-iam-policy-binding $secretName `
        --project=$ProjectId `
        --member="serviceAccount:$ServiceAccountEmail" `
        --role="roles/secretmanager.secretAccessor" `
        --quiet *> $null
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to grant Secret Manager access: $secretName"
    }
}

Set-Clipboard -Value $password

Write-Output "PASSWORD_COPIED_TO_CLIPBOARD=True"
Write-Output "PASSWORD_SECRET=$passwordSecret"
Write-Output "SESSION_SECRET=$sessionSecret"
