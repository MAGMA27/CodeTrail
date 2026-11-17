# Source panel restoration regression

`loadOverview` clears module definition list state only. `resetModuleSymbols` still clears both module list and source panel for module, project, and index invalidation paths.

The regression test parses `workbench.tsx` with the installed TypeScript compiler API and mounts a React seam with `react-test-renderer`. It executes the production `resetModuleSymbolSource`, `resetModuleSymbolListState`, `resetModuleSymbols`, and `loadOverview` callbacks plus the production index-change effect. API overview and source reads are controlled test promises; unrelated workbench UI and API imports are not mounted.

Verification: `node --test app/module-symbol-state.test.mjs` passes 4 mounted tests for source restoration and successful read, explicit source close cancellation, project/index reset invalidation, and project/index remount cancellation. With only `loadOverview` temporarily switched back to `resetModuleSymbols`, the restoration test fails because the panel is `none` and the pending source read is aborted; the production file was restored in a `finally` block before the fixed run. This seam does not mount the full Workbench or independently verify browser rendering or Session URL restoration.
