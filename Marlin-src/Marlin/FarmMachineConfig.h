/**
 * 3D Print Farm - machine-specific configuration
 *
 * Edit this file for each custom printer. Keep network / farm protocol
 * settings outside this file so mechanical variants remain easy to manage.
 *
 * IMPORTANT: Values below are placeholders copied from the original generic
 * Marlin configuration. Do not flash or operate a printer until every value
 * has been measured, reviewed, and FARM_MACHINE_CONFIG_VERIFIED is set to 1.
 */
#pragma once

#define FARM_MACHINE_CONFIG_VERSION 1
#define FARM_MACHINE_CONFIG_VERIFIED 0

#define FARM_MACHINE_NAME "Farma Custom"

// Printable volume in millimeters - TODO: measure the custom printer.
#define FARM_BED_SIZE_X 200
#define FARM_BED_SIZE_Y 200
#define FARM_Z_MAX_POS  200

// Motion - TODO: calculate and test for the installed mechanics.
#define FARM_AXIS_STEPS_PER_UNIT { 80, 80, 400, 500 }
#define FARM_MAX_FEEDRATE        { 300, 300, 5, 25 }
#define FARM_MAX_ACCELERATION    { 3000, 3000, 100, 10000 }

// Axis direction - TODO: verify at low speed with belts / leadscrews released
// where practical. Wrong values can drive an axis away from its endstop.
#define FARM_INVERT_X_DIR false
#define FARM_INVERT_Y_DIR true
#define FARM_INVERT_Z_DIR false
#define FARM_INVERT_E0_DIR false

// Thermal configuration - TODO: identify the actual sensors and safe limits.
#define FARM_TEMP_SENSOR_HOTEND 1
#define FARM_TEMP_SENSOR_BED    1
#define FARM_HOTEND_MAXTEMP     275
#define FARM_BED_MAXTEMP        150

// BIQU MicroProbe. Set to 1 or 2 only after checking the probe label.
// V1 triggers HIGH, V2 triggers LOW. Zero keeps probe support disabled.
#define FARM_MICROPROBE_VERSION 0

// Probe position relative to nozzle in mm - TODO: measure on the machine.
#define FARM_PROBE_OFFSET_X 10
#define FARM_PROBE_OFFSET_Y 10
#define FARM_PROBE_OFFSET_Z 0
#define FARM_PROBING_MARGIN 10

#if !FARM_MACHINE_CONFIG_VERIFIED
  #warning "Farm machine parameters are placeholders. DO NOT FLASH for production use."
#endif

#if FARM_MICROPROBE_VERSION < 0 || FARM_MICROPROBE_VERSION > 2
  #error "FARM_MICROPROBE_VERSION must be 0, 1, or 2."
#endif

