/* 3DPrintFarm extension for ESP3D. */
#include "farm_agent.h"

#ifdef FARM_AGENT_FEATURE

#include <HTTPClient.h>
#include <WiFi.h>

#include "../../core/esp3d_log.h"
#include "../filesystem/esp_sd.h"

FarmAgent farm_agent;
uint32_t FarmAgent::_nextPoll = 0;
bool FarmAgent::_busy = false;
String FarmAgent::_activeJobId;

void FarmAgent::begin() {
  _nextPoll = millis() + 5000;
  esp3d_log("FarmAgent initialized");
}

void FarmAgent::handle() {
  if (_busy || WiFi.status() != WL_CONNECTED || FARM_AGENT_API_KEY[0] == '\0') return;
  const uint32_t current = millis();
  if ((int32_t)(current - _nextPoll) < 0) return;
  _nextPoll = current + FARM_AGENT_POLL_INTERVAL_MS;
  poll();
}

bool FarmAgent::postJson(const String& path, const String& body, String* response, int* code) {
  HTTPClient http;
  const String url = String(FARM_AGENT_SERVER_URL) + path;
  if (!http.begin(url)) return false;
  http.setTimeout(5000);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Printer-Key", FARM_AGENT_API_KEY);
  const int result = http.POST(body);
  if (code) *code = result;
  if (response && result > 0 && result != HTTP_CODE_NO_CONTENT) *response = http.getString();
  http.end();
  return result >= 200 && result < 300;
}

void FarmAgent::poll() {
  _busy = true;
  postJson("/api/printer/telemetry", "{\"status\":\"idle\",\"progress\":0}");

  if (FARM_AGENT_AUTO_CLAIM && _activeJobId.length() == 0) {
    String response;
    int code = 0;
    if (postJson("/api/printer/next-job", "{}", &response, &code) && code != HTTP_CODE_NO_CONTENT) {
      const String jobId = jsonValue(response, "id");
      const String name = jsonValue(response, "name");
      const String downloadUrl = jsonValue(response, "download_url");
      if (jobId.length() && downloadUrl.length()) {
        _activeJobId = jobId;
        reportJobState(jobId, "downloading");
        if (downloadJob(jobId, downloadUrl, name)) {
          reportJobState(jobId, "ready");
          esp3d_log("Farm job ready: %s", jobId.c_str());
        } else {
          reportJobState(jobId, "error", 0, "SD download failed");
          _activeJobId = "";
        }
      }
    }
  }
  _busy = false;
}

bool FarmAgent::downloadJob(const String& jobId, const String& downloadUrl, const String& originalName) {
  HTTPClient http;
  String url = downloadUrl;
  if (!url.startsWith("http://") && !url.startsWith("https://")) url = String(FARM_AGENT_SERVER_URL) + url;
  if (!http.begin(url)) return false;
  http.setTimeout(15000);
  http.addHeader("X-Printer-Key", FARM_AGENT_API_KEY);
  const int result = http.GET();
  if (result != HTTP_CODE_OK) {
    http.end();
    return false;
  }

  String safeName = originalName;
  safeName.replace("/", "_");
  safeName.replace("\\", "_");
  if (!safeName.length()) safeName = jobId + ".gcode";
  const String finalPath = "/farm_" + jobId + "_" + safeName;
  const String tempPath = finalPath + ".part";

  ESP_SD::remove(tempPath.c_str());
  ESP_SDFile file = ESP_SD::open(tempPath.c_str(), ESP_FILE_WRITE);
  if (!file) {
    http.end();
    return false;
  }

  WiFiClient* stream = http.getStreamPtr();
  uint8_t buffer[1024];
  int remaining = http.getSize();
  bool ok = true;
  while (http.connected() && (remaining > 0 || remaining == -1)) {
    const size_t available = stream->available();
    if (available) {
      const size_t count = stream->readBytes(buffer, min(available, sizeof(buffer)));
      if (file.write(buffer, count) != count) {
        ok = false;
        break;
      }
      if (remaining > 0) remaining -= count;
    } else {
      delay(1);
    }
  }
  file.flush();
  file.close();
  http.end();

  if (!ok || remaining > 0) {
    ESP_SD::remove(tempPath.c_str());
    return false;
  }
  ESP_SD::remove(finalPath.c_str());
  return ESP_SD::rename(tempPath.c_str(), finalPath.c_str());
}

void FarmAgent::reportJobState(const String& jobId, const char* state, float progress, const char* message) {
  String body = String("{\"state\":\"") + state + "\",\"progress\":" + String(progress, 1);
  if (message) body += String(",\"message\":\"") + message + "\"";
  body += "}";
  postJson(String("/api/printer/jobs/") + jobId + "/state", body);
}

String FarmAgent::jsonValue(const String& json, const char* key) {
  const String marker = String("\"") + key + "\"";
  int position = json.indexOf(marker);
  if (position < 0) return "";
  position = json.indexOf(':', position + marker.length());
  if (position < 0) return "";
  position = json.indexOf('"', position + 1);
  if (position < 0) return "";
  const int end = json.indexOf('"', position + 1);
  if (end < 0) return "";
  return json.substring(position + 1, end);
}

#endif  // FARM_AGENT_FEATURE
