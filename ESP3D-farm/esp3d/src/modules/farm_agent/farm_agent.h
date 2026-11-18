/* 3DPrintFarm extension for ESP3D. */
#ifndef FARM_AGENT_H
#define FARM_AGENT_H

#include "../../include/esp3d_config.h"

#ifdef FARM_AGENT_FEATURE
class FarmAgent {
 public:
  static void begin();
  static void handle();

 private:
  static uint32_t _nextPoll;
  static bool _busy;
  static String _activeJobId;
  static String jsonValue(const String& json, const char* key);
  static bool postJson(const String& path, const String& body, String* response = nullptr, int* code = nullptr);
  static void poll();
  static bool downloadJob(const String& jobId, const String& downloadUrl, const String& originalName);
  static void reportJobState(const String& jobId, const char* state, float progress = 0, const char* message = nullptr);
};

extern FarmAgent farm_agent;
#endif  // FARM_AGENT_FEATURE
#endif  // FARM_AGENT_H
