param(
    [int]$Epochs = 60,
    [int]$Batch = 4
)
$ErrorActionPreference = "Stop"

Write-Host "=== YOLO 训练 + 导出 + RKNN 转换（WSL rknn 环境）===" -ForegroundColor Cyan
Write-Host "epochs=$Epochs batch=$Batch" -ForegroundColor Yellow

wsl -d Ubuntu -- bash /mnt/d/GongChuang_Advanced/tools/run_yolo_wsl.sh $Epochs $Batch
if ($LASTEXITCODE -ne 0) {
    throw "流程失败，exit=$LASTEXITCODE"
}

Write-Host "=== 完成，RKNN 在 D:\GongChuang_Advanced\models_new ===" -ForegroundColor Green
