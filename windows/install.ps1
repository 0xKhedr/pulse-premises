# Smart Building Management System - Windows Installation Script
# Run this script from PowerShell

# Set working directory to the project root
Set-Location -Path "$PSScriptRoot\.."

Write-Host "==========================================================" -ForegroundColor Cyan
Write-Host "Installing Smart Building Management System (Windows)" -ForegroundColor Cyan
Write-Host "==========================================================" -ForegroundColor Cyan

# 1. Check Python
Write-Host "[1/4] Checking Python installation..." -ForegroundColor Yellow
if (-not (Get-Command "python" -ErrorAction SilentlyContinue)) {
    Write-Error "Python is not installed or not added to PATH. Please install Python 3.8+ before running this script."
    exit 1
}

# 2. Virtual Environment
Write-Host "[2/4] Setting up Python virtual environment..." -ForegroundColor Yellow
python -m venv venv

# 3. Python Dependencies
Write-Host "[3/4] Installing Python requirements..." -ForegroundColor Yellow
# Using explicit paths to avoid ExecutionPolicy issues with Activate.ps1
.\venv\Scripts\python -m pip install --upgrade pip
.\venv\Scripts\python -m pip install -r requirements.txt

# 4. Directories setup
Write-Host "[4/4] Creating necessary directories..." -ForegroundColor Yellow
if (-not (Test-Path -Path "uploads")) {
    New-Item -ItemType Directory -Force -Path "uploads" | Out-Null
}

Write-Host "==========================================================" -ForegroundColor Green
Write-Host "Installation Complete!" -ForegroundColor Green
Write-Host "==========================================================" -ForegroundColor Green
Write-Host "To start the application, run the following command:"
Write-Host "  .\windows\run.bat"
Write-Host "==========================================================" -ForegroundColor Green
