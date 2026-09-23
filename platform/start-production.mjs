import { spawn } from 'node:child_process';

const processes = [
  spawn(process.execPath, ['/app/apps/api/dist/main.js'], { stdio: 'inherit' }),
  spawn('python3', ['/app/worker/worker.py'], { stdio: 'inherit' }),
];

let stopping = false;
function stop(signal = 'SIGTERM') {
  if (stopping) return;
  stopping = true;
  for (const child of processes) child.kill(signal);
}

for (const signal of ['SIGTERM', 'SIGINT']) {
  process.on(signal, () => stop(signal));
}

for (const child of processes) {
  child.on('error', error => {
    console.error('Service process failed to start:', error);
    stop();
    process.exitCode = 1;
  });
  child.on('exit', (code, signal) => {
    if (!stopping) {
      console.error(`Service process exited (${signal || code}).`);
      stop();
      process.exitCode = code || 1;
    }
  });
}
