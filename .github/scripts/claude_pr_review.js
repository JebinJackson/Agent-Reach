const fs = require('fs');

const diffPath = process.env.DIFF_PATH || 'pr.diff';
const apiKey = process.env.ANTHROPIC_API_KEY;
const repo = process.env.REPOSITORY;
const prNumber = process.env.PR_NUMBER;
const githubToken = process.env.GITHUB_TOKEN;
const model = process.env.MODEL || 'claude-3-5-sonnet-20241022';
const maxDiffChars = 20000;

function loadDiff() {
  if (!fs.existsSync(diffPath)) {
    return '';
  }

  let diff = fs.readFileSync(diffPath, 'utf8');
  if (!diff.trim()) {
    return '';
  }

  if (diff.length > maxDiffChars) {
    return diff.slice(0, maxDiffChars) + '\n\n... [diff truncated for review]';
  }

  return diff;
}

async function callClaude(prompt) {
  if (!apiKey) {
    throw new Error('ANTHROPIC_API_KEY is not set. Add it as a repository secret.');
  }

  const response = await fetch('https://api.anthropic.com/v1/messages', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'x-api-key': apiKey,
      'anthropic-version': '2023-06-01',
    },
    body: JSON.stringify({
      model,
      max_tokens: 1024,
      system: 'You are a senior software reviewer. Focus on correctness, security, maintainability, and edge cases. Be concise, actionable, and practical.',
      messages: [
        {
          role: 'user',
          content: prompt,
        },
      ],
    }),
  });

  const responseText = await response.text();

  if (!response.ok) {
    throw new Error(`Claude API request failed (${response.status}): ${responseText}`);
  }

  const data = JSON.parse(responseText);
  return (data.content || [])
    .map((item) => item.type === 'text' ? item.text : '')
    .join('\n')
    .trim();
}

async function postCommentToPr(body) {
  if (!githubToken) {
    throw new Error('GITHUB_TOKEN is not available in this job.');
  }

  if (!repo || !prNumber) {
    throw new Error('REPOSITORY and PR_NUMBER must be set.');
  }

  const response = await fetch(`https://api.github.com/repos/${repo}/issues/${prNumber}/comments`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${githubToken}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
      'Content-Type': 'application/json',
    },
    body: JSON.stringify({
      body,
    }),
  });

  const responseText = await response.text();

  if (!response.ok) {
    throw new Error(`Failed to post GitHub review comment (${response.status}): ${responseText}`);
  }
}

async function main() {
  const diff = loadDiff();

  if (!diff.trim()) {
    console.log('No diff found. Skipping Claude review.');
    return;
  }

  const prompt = `Review this pull request diff and provide a concise code review.

Focus on:
- correctness and logic bugs
- security or privacy issues
- maintainability and code quality
- edge cases or missing validation
- obvious performance concerns

Return:
1. A short summary of the change
2. 3-5 bullet points of findings, if any; otherwise say "No obvious issues found."
3. Clear, actionable suggestions for fixes

PR diff:
\n\n${diff}`;

  const review = await callClaude(prompt);
  const comment = `## Claude PR Review\n\n${review}`;

  await postCommentToPr(comment);
  console.log('Claude review comment posted successfully.');
}

main().catch((error) => {
  console.error(error.message || error);
  process.exit(1);
});
