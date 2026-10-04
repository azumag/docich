import { open, readFile, stat, unlink } from "node:fs/promises";
import { resolve } from "node:path";
import { parseDataset, report, trainCandidate } from "../src/training/index.js";

const USAGE = `Usage:
  node scripts/train-brain.mjs report --input games.jsonl --output report.json
  node scripts/train-brain.mjs report --input games.jsonl --output comparison.json --baseline base.json --candidate candidate.json --training-report training-report.json
  node scripts/train-brain.mjs candidate --input games.jsonl --profile base.json --output candidate.json --report training-report.json

Optional training split parameters (comparison reads the saved training report):
  --split-seed tsuitate-v1 --holdout-fraction 0.2
Candidate-only options:
  --min-games 10 --learning-rate 0.1 --max-step 0.05 --foul-penalty 0.5

Outputs must be new files. This command does not start games, deploy, or promote profiles.
`;

function fail(code) {
  const error = new Error(code);
  error.code = code;
  throw error;
}

function argumentsFor(argv) {
  if (argv.length === 1 && ["--help", "-h"].includes(argv[0])) return { help: true };
  const [command, ...rest] = argv;
  if (!["report", "candidate"].includes(command)) fail("invalid_command");
  const allowed = new Set(["input", "output", "split-seed", "holdout-fraction",
    ...(command === "candidate" ? ["profile", "report", "min-games", "learning-rate", "max-step", "foul-penalty"] : ["baseline", "candidate", "training-report"])]);
  const options = {};
  for (let index = 0; index < rest.length; index += 2) {
    const key = rest[index]?.replace(/^--/, "");
    const value = rest[index + 1];
    if (!rest[index]?.startsWith("--") || !allowed.has(key) || typeof value !== "string" || value.startsWith("--") || Object.hasOwn(options, key)) fail("invalid_arguments");
    options[key] = value;
  }
  if (!options.input || !options.output || (command === "candidate" && (!options.profile || !options.report))) fail("missing_required_path");
  if (command === "report" && [options.baseline, options.candidate, options["training-report"]].some(Boolean) &&
    ![options.baseline, options.candidate, options["training-report"]].every(Boolean)) fail("comparison_requires_profiles_and_training_report");
  return { command, options };
}

async function readBounded(path, limit) {
  const metadata = await stat(path);
  if (!metadata.isFile() || metadata.size > limit) fail("invalid_input_file");
  const value = await readFile(path, "utf8");
  if (Buffer.byteLength(value) > limit) fail("input_too_large");
  return value;
}

async function readProfile(path) {
  try {
    return JSON.parse(await readBounded(path, 65536));
  } catch (error) {
    if (error.code) throw error;
    fail("invalid_profile_json");
  }
}

async function readTrainingReport(path) {
  try {
    return JSON.parse(await readBounded(path, 8 * 1024 * 1024));
  } catch (error) {
    if (error.code) throw error;
    fail("invalid_training_report_json");
  }
}

function numericOptions(options) {
  const result = {};
  if (options["split-seed"] !== undefined) result.seed = options["split-seed"];
  for (const [flag, key] of Object.entries({ "holdout-fraction": "holdoutFraction", "min-games": "minGames", "learning-rate": "learningRate", "max-step": "maxStep", "foul-penalty": "foulPenalty" })) {
    if (options[flag] !== undefined) {
      if (!/^(?:\d+\.?\d*|\.\d+)$/.test(options[flag])) fail("invalid_numeric_option");
      result[key] = Number(options[flag]);
    }
  }
  return result;
}

async function writeNewFiles(outputs) {
  const handles = [];
  try {
    for (const [path, content] of outputs) handles.push({ path, content, handle: await open(path, "wx", 0o600) });
    for (const entry of handles) {
      await entry.handle.writeFile(`${JSON.stringify(entry.content, null, 2)}\n`, "utf8");
      await entry.handle.sync();
    }
    for (const entry of handles) await entry.handle.close();
  } catch (error) {
    for (const entry of handles) {
      await entry.handle.close().catch(() => {});
      await unlink(entry.path).catch(() => {});
    }
    throw error;
  }
}

async function main() {
  const args = argumentsFor(process.argv.slice(2));
  if (args.help) {
    process.stdout.write(USAGE);
    return;
  }
  const { command, options } = args;
  const { records, duplicatesDropped } = parseDataset(await readBounded(options.input, 64 * 1024 * 1024));
  const settings = numericOptions(options);
  let outputs;
  if (command === "report") {
    if (options.baseline) {
      settings.baselineProfile = await readProfile(options.baseline);
      settings.candidateProfile = await readProfile(options.candidate);
      settings.trainingProvenance = (await readTrainingReport(options["training-report"]))?.provenance;
    }
    outputs = [[options.output, { ...report(records, settings), duplicatesDropped }]];
  } else {
    const result = trainCandidate(records, await readProfile(options.profile), settings);
    const { profile, ...trainingReport } = result;
    trainingReport.provenance.duplicatesDropped = duplicatesDropped;
    outputs = [[options.output, profile], [options.report, trainingReport]];
  }
  if (new Set(outputs.map(([path]) => resolve(path))).size !== outputs.length) fail("output_paths_must_differ");
  await writeNewFiles(outputs);
  process.stdout.write(`${JSON.stringify({ status: "written", command, files: outputs.length, records: records.length, duplicatesDropped, automaticPromotion: false })}\n`);
}

main().catch((error) => {
  // Do not leak input data, credentials embedded in paths, or arbitrary raw error messages.
  const code = typeof error?.code === "string" && /^[a-zA-Z0-9_]+$/.test(error.code) ? error.code : "training_command_failed";
  process.stderr.write(`${JSON.stringify({ error: code })}\n`);
  process.exitCode = 1;
});
