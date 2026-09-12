export type SessionDraftSubmission = {
  projectId: string;
  sessionId: string;
  draftRevision: number;
};

export function nextSessionDraftRevision(currentRevision: number | undefined): number {
  return (currentRevision ?? 0) + 1;
}

export function shouldClearSubmittedSessionDraft(
  current: {
    projectId: string | null;
    sessionId: string | null;
    draftRevision: number | undefined;
  },
  submission: SessionDraftSubmission,
): boolean {
  return current.projectId === submission.projectId &&
    current.sessionId === submission.sessionId &&
    (current.draftRevision ?? 0) === submission.draftRevision;
}
