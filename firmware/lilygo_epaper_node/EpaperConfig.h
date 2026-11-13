#pragma once

// Rev 2.1 is the adapter PCB revision, not the panel controller. Default to
// the older Waveshare 4.2-inch (B), GDEW042Z15 / IL0398. Match the panel label:
// 1 = GDEW042Z15 (GxEPD2_420c)
// 2 = GDEQ042Z21 (GxEPD2_420c_Z21)
// 3 = GDEY042Z98 (GxEPD2_420c_GDEY042Z98)
#ifndef SBMS_EPAPER_PANEL
#define SBMS_EPAPER_PANEL 1
#endif

// Dedicated HSPI bus: never change the onboard SX1276's SPI wiring.
constexpr int EPAPER_CLK = 14;
constexpr int EPAPER_DIN = 13;
constexpr int EPAPER_CS = 15;
constexpr int EPAPER_DC = 25;
constexpr int EPAPER_RST = 4;
constexpr int EPAPER_BUSY = 34;
