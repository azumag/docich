// Public browser distribution: never pull Node's ws/XMLHttpRequest transports
// into workerd. Outbound sockets cannot use DO WebSocket hibernation.
import io from "socket.io-client/dist/socket.io.js";

export function createWorkerSocket(token) {
  return io("https://beta.tsuitate.info", {
    auth: { token }, transports: ["websocket"], autoConnect: false,
    forceNew: true, multiplex: false, reconnection: true,
    reconnectionAttempts: 12, reconnectionDelay: 1000,
    reconnectionDelayMax: 5000, timeout: 5000,
  });
}
