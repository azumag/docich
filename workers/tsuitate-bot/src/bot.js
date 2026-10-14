// Existing consumers retain the CSA interface; all policy lives in the common brain.
import { chooseWebhookDecision } from "./adapters/webhook.js";
export { parseVisibleSfen } from "./adapters/webhook.js";

export function chooseObservedMove(options) {
  return chooseWebhookDecision(options)?.move ?? null;
}
