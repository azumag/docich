import webhook from "./index.js";
import { CONTROL_PATH, handleBetaControl } from "./arena/control.js";
export { GameState, authenticateRequest } from "./index.js";
export { BetaArena } from "./arena/beta-arena.js";

export default {
  fetch(request, env) {
    return new URL(request.url).pathname === CONTROL_PATH ? handleBetaControl(request, env) : webhook.fetch(request, env);
  },
};
