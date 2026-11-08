/** MediaPipe geometry is in full-source-image pixels, independent of box timing. */

const MAX_AGE_MS = 1500;
const POSE_CONNECTIONS = [
  [0, 1], [1, 2], [2, 3], [3, 7], [0, 4], [4, 5], [5, 6], [6, 8], [9, 10],
  [11, 12], [11, 13], [13, 15], [15, 17], [15, 19], [15, 21], [17, 19],
  [12, 14], [14, 16], [16, 18], [16, 20], [16, 22], [18, 20],
  [11, 23], [12, 24], [23, 24], [23, 25], [24, 26], [25, 27], [26, 28],
  [27, 29], [28, 30], [29, 31], [30, 32], [27, 31], [28, 32],
];
const HAND_CONNECTIONS = [
  [0, 1], [1, 2], [2, 3], [3, 4], [0, 5], [5, 6], [6, 7], [7, 8],
  [5, 9], [9, 10], [10, 11], [11, 12], [9, 13], [13, 14], [14, 15], [15, 16],
  [13, 17], [0, 17], [17, 18], [18, 19], [19, 20],
];
const LIP_LOOPS = [
  [61, 146, 91, 181, 84, 17, 314, 405, 321, 375, 291, 409, 270, 269, 267, 0, 37, 39, 40, 185, 61],
  [78, 95, 88, 178, 87, 14, 317, 402, 318, 324, 308, 415, 310, 311, 312, 13, 82, 81, 80, 191, 78],
];

export function acceptLandmarkFrame(current, incoming) {
  if (!incoming || !Array.isArray(incoming.keypoints)) return current;
  const complete = incoming.type === "keypoints" || incoming.completed?.includes("keypoints")
    || incoming.keypoints.length > 0;
  if (!complete || !Number.isInteger(incoming.seq) || !Number.isFinite(incoming.timestamp)) return current;
  if (current && incoming.seq < current.seq) return current;
  return incoming;
}

export function freshLandmarkFrame(frame, nowMs) {
  if (!frame || !Number.isFinite(frame.timestamp)) return null;
  const age = nowMs - frame.timestamp * 1000;
  return age >= 0 && age <= MAX_AGE_MS ? frame : null;
}

export function landmarkStatus(frame, nowMs) {
  if (!frame) return "Landmarks: waiting";
  if (!freshLandmarkFrame(frame, nowMs)) return "Landmarks: stale";
  if (frame.errors?.some((error) => error.startsWith("keypoints:"))) return "Landmarks: unavailable";
  const counts = { body: 0, face: 0, hands: 0, lips: 0 };
  for (const result of frame.keypoints || []) {
    const count = Array.isArray(result.points) ? result.points.length : 0;
    if (result.schema === "mediapipe_pose33") counts.body += count;
    else if (result.schema?.startsWith("mediapipe_face")) counts.face += count;
    else if (result.schema?.endsWith("hand21")) counts.hands += count;
    else if (result.schema === "mediapipe_lips") counts.lips += count;
  }
  if (!Object.values(counts).some(Boolean)) return "Landmarks: none detected";
  return `Landmarks: body ${counts.body} · face ${counts.face} · lips ${counts.lips} · hands ${counts.hands}`;
}

export function drawLandmarkOverlay(ctx, canvasWidth, canvasHeight, frame, options = {}) {
  if (!frame || !Array.isArray(frame.keypoints) || !(frame.width > 0) || !(frame.height > 0)) return;
  const scaleX = canvasWidth / frame.width;
  const scaleY = canvasHeight / frame.height;
  const visibleTracks = options.visibleTrackIds;
  const project = (point, body = false) => {
    if (!Array.isArray(point) || !Number.isFinite(point[0]) || !Number.isFinite(point[1])) return null;
    if (body && (!Number.isFinite(point[3]) || point[3] < 0.5)) return null;
    if (point[0] < 0 || point[0] > frame.width || point[1] < 0 || point[1] > frame.height) return null;
    const x = point[0] * scaleX;
    return [options.mirrored ? canvasWidth - x : x, point[1] * scaleY];
  };
  ctx.save();
  try {
    for (const result of frame.keypoints) {
      if (visibleTracks && !visibleTracks.has(result.track_id)) continue;
      if (!Array.isArray(result.points)) continue;
      const body = result.schema === "mediapipe_pose33";
      const hand = result.schema?.endsWith("hand21");
      const lips = result.schema === "mediapipe_lips";
      const face = result.schema?.startsWith("mediapipe_face");
      if (!body && !hand && !lips && !face) continue;
      const points = result.points.map((point) => project(point, body));
      ctx.strokeStyle = lips ? "#fb7185" : hand ? "#fbbf24" : "#22d3ee";
      ctx.fillStyle = face ? "rgba(147,197,253,0.65)" : ctx.strokeStyle;
      ctx.lineWidth = lips ? 2 : 1.5;
      let connections = body ? POSE_CONNECTIONS : hand ? HAND_CONNECTIONS : [];
      if (lips) {
        const indices = new Map((result.landmark_indices || []).map((index, position) => [index, position]));
        connections = LIP_LOOPS.flatMap((loop) => loop.slice(1).map((index, position) => [indices.get(loop[position]), indices.get(index)]));
      }
      for (const [start, end] of connections) {
        if (!points[start] || !points[end]) continue;
        ctx.beginPath();
        ctx.moveTo(...points[start]);
        ctx.lineTo(...points[end]);
        ctx.stroke();
      }
      for (const point of points) {
        if (!point) continue;
        ctx.beginPath();
        ctx.arc(point[0], point[1], face ? 1 : lips ? 1.5 : 2.5, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  } finally {
    ctx.restore();
  }
}
