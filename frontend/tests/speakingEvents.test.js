import test from "node:test";
import assert from "node:assert/strict";
import { SpeakingEvents } from "../src/cv/speakingEvents.js";
const alex = { track_id: 1, display_name: "Alex", generation: 10, source_id: "camera" };
const sam = { track_id: 2, display_name: "Sam", generation: 20, source_id: "camera" };
const frame = (timestamp, values) => ({ timestamp: timestamp / 1000,
  keypoints: values.map((mouth_motion, index) => ({ track_id: index + 1,
    schema: "mediapipe_lips", mouth_motion })) });

test("one Space hold emits exactly one pair despite pauses and missing landmarks", () => {
  const detector = new SpeakingEvents();
  assert.deepEqual(detector.start([alex]), [{ event_type: "speaking", phase: "start", track_id: 1, display_name: "Alex" }]);
  assert.deepEqual(detector.start([alex]), []);
  for (let timestamp = 0; timestamp < 15000; timestamp += 100) {
    detector.addAudio({ timestamp, rms: timestamp % 2000 < 1000 ? .1 : 0 });
    assert.deepEqual(detector.update(timestamp % 500 ? null : frame(timestamp, [.003]), [alex], timestamp), []);
  }
  assert.deepEqual(detector.stop(), [{ event_type: "speaking", phase: "end", track_id: 1, display_name: "Alex" }]);
  assert.deepEqual(detector.stop(), []);
  assert.deepEqual(detector.update(frame(16000, [.003]), [alex], 16000), []);
});

test("the utterance keeps its speaker if the camera track temporarily disappears", () => {
  const detector = new SpeakingEvents();
  detector.start([alex]);
  assert.deepEqual(detector.update(null, [], 10000), []);
  assert.equal(detector.stop()[0].display_name, "Alex");
});

test("multiple visible people stay unknown without correlated microphone and lip evidence", () => {
  const detector = new SpeakingEvents();
  assert.equal(detector.start([alex, sam])[0].display_name, "Unknown speaker");
  detector.update(frame(1000, [.003, .001]), [alex, sam], 1000);
  assert.equal(detector.snapshot(), null);
  detector.addAudio({ timestamp: 1100, rms: .1 });
  detector.update(frame(1100, [.003, .003]), [alex, sam], 1100);
  assert.equal(detector.snapshot(), null);
  detector.addAudio({ timestamp: 1200, rms: .1 });
  detector.update(frame(1200, [.004, .001]), [alex, sam], 1200);
  assert.deepEqual(detector.snapshot(), { track_id: 1, generation: 10, source_id: "camera", method: "lip_motion", display_name: "Alex" });
  assert.equal(detector.stop()[0].display_name, "Alex");
});

test("a fresh hold resets attribution and a camera reconnect does not split the hold", () => {
  const detector = new SpeakingEvents();
  detector.start([alex]);
  detector.stop();
  assert.equal(detector.start([sam])[0].display_name, "Sam");
  detector.cameraReset();
  assert.deepEqual(detector.update(null, [sam], 1000), []);
  assert.equal(detector.stop()[0].display_name, "Sam");
});

test("late CV frames cannot use unrelated audio to attribute a speaker", () => {
  const detector = new SpeakingEvents();
  detector.start([alex, sam]);
  detector.addAudio({ timestamp: 5000, rms: .1 });
  detector.update(frame(1000, [.004, .001]), [alex, sam], 5000);
  assert.equal(detector.snapshot(), null);
});
