// Compatibility barrel. Domain code lives in focused clients so adding a new
// provider or control surface does not expand a single all-purpose API module.
export * from "./runtime-client/companionClient";
export * from "./runtime-client/channelsClient";
export * from "./runtime-client/coreClient";
export * from "./runtime-client/mcpClient";
export * from "./runtime-client/memoryClient";
export * from "./runtime-client/modelsClient";
export * from "./runtime-client/sessionsClient";
export * from "./runtime-client/skillsClient";
export * from "./runtime-client/ttsClient";
export * from "./runtime-client/workerPacksClient";
