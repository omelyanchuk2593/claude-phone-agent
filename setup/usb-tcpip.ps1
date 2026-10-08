# Включить ADB по сети на телефоне. Запускать на КОМПЬЮТЕРЕ с Windows,
# телефон подключён USB-кабелем, «Отладка по USB» включена.
#
#     powershell -ExecutionPolicy Bypass -File usb-tcpip.ps1
#
# Если adb ещё нет, скрипт сам скачает официальные platform-tools от Google
# в %USERPROFILE%\platform-tools. Повторять после каждой перезагрузки телефона.

$dir = Join-Path $env:USERPROFILE "platform-tools"
$adb = Join-Path $dir "adb.exe"

if (-not (Test-Path $adb)) {
    Write-Host "Скачиваю platform-tools (adb) с сайта Google..."
    $zip = Join-Path $env:TEMP "platform-tools.zip"
    Invoke-WebRequest "https://dl.google.com/android/repository/platform-tools-latest-windows.zip" -OutFile $zip -UseBasicParsing
    Expand-Archive $zip -DestinationPath $env:USERPROFILE -Force
    Remove-Item $zip
}

& $adb start-server | Out-Null
Write-Host "Жду телефон по USB. Если на экране телефона появился вопрос «Разрешить отладку по USB?» —"
Write-Host "поставь галочку «Всегда разрешать с этого компьютера» и нажми «Разрешить»."

$state = ""
for ($i = 0; $i -lt 60; $i++) {
    $state = (& $adb -d get-state 2>$null | Out-String).Trim()
    if ($state -eq "device") { break }
    Start-Sleep -Seconds 2
}
if ($state -ne "device") {
    Write-Host ""
    Write-Host "Телефон так и не появился. Проверь:"
    Write-Host " - кабель передаёт данные (некоторые кабели только заряжают);"
    Write-Host " - на телефоне включена «Отладка по USB»;"
    Write-Host " - вопрос «Разрешить отладку?» на телефоне подтверждён."
    & $adb devices
    exit 1
}

$model = (& $adb -d shell getprop ro.product.model | Out-String).Trim()
Write-Host "Телефон найден: $model"
& $adb -d tcpip 5555
Start-Sleep -Seconds 2
Write-Host ""
Write-Host "Готово! ADB по сети включён на порту 5555. Кабель можно отключать."
Write-Host "Дальше — на сервере: phone connect"
