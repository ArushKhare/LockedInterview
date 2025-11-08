// server.js
// Backend for LockedInterview – returns questions from in-memory banks (no scraping)

const express = require("express");
const cors = require("cors");
const path = require("path");

const app = express();
const PORT = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());

// Serve your frontend.html (and any other static files) from this folder
app.use(express.static(__dirname));

/* ------------------------------------------------------------------ */
/* QUESTION BANKS                                                     */
/* ------------------------------------------------------------------ */

// Behavioral – generic for any role
const BEHAVIORAL_GENERIC = [
  {
    text: "Tell me about a time you disagreed with a teammate and how you resolved it.",
    tags: ["Behavioral", "Conflict"],
  },
  {
    text: "Describe a project you’re most proud of. What was your role and what was the outcome?",
    tags: ["Behavioral", "Ownership"],
  },
  {
    text: "Tell me about a time you had to learn something quickly to succeed.",
    tags: ["Behavioral", "Learning"],
  },
  {
    text: "Walk me through a time when you made a mistake at work or school. What did you do afterwards?",
    tags: ["Behavioral", "Failure"],
  },
  {
    text: "Give an example of working under a tight deadline. How did you manage priorities?",
    tags: ["Behavioral", "Pressure"],
  },
  {
    text: "Tell me about a time you had to influence someone without direct authority.",
    tags: ["Behavioral", "Influence"],
  },
  {
    text: "Describe a time you received tough feedback. How did you respond?",
    tags: ["Behavioral", "Feedback"],
  },
  {
    text: "Tell me about a situation where you had to deal with ambiguity.",
    tags: ["Behavioral", "Ambiguity"],
  },
];

// Technical – SWE / FAANG-ish, Intern / Junior flavor
const TECH_SWE_FAANG_INTERN = [
  {
    text:
      "Given an array of integers and a target value, return indices of any two numbers whose sum equals the target.",
    tags: ["Technical", "Arrays"],
  },
  {
    text:
      "Given a string, determine if it contains all unique characters. Try solving it with and without extra data structures.",
    tags: ["Technical", "Strings"],
  },
  {
    text:
      "Given a singly linked list, reverse the list in place and return the new head.",
    tags: ["Technical", "Linked List"],
  },
  {
    text:
      "Given an array of integers, move all zeros to the end while keeping the relative order of non-zero elements.",
    tags: ["Technical", "Arrays", "Two Pointers"],
  },
  {
    text:
      "Implement a function that checks whether two strings are anagrams of each other.",
    tags: ["Technical", "Strings", "Hash Map"],
  },
  {
    text:
      "Given a sorted array of integers and a target, return the index of the target or the position where it should be inserted.",
    tags: ["Technical", "Binary Search"],
  },
  {
    text:
      "Design a data structure that supports push, pop, top, and retrieving the minimum element in constant time.",
    tags: ["Technical", "Stack", "Design"],
  },
  {
    text:
      "Given the root of a binary tree, return its level-order traversal (values by level).",
    tags: ["Technical", "Trees", "BFS"],
  },
  {
    text:
      "Given an array of stock prices, compute the maximum profit from a single buy and a single sell.",
    tags: ["Technical", "Arrays"],
  },
  {
    text:
      "You are given a staircase with n steps and you can climb 1 or 2 steps at a time. How many distinct ways can you climb to the top?",
    tags: ["Technical", "Dynamic Programming"],
  },
];

// Technical – SWE generic (non-FAANG / fallback)
const TECH_SWE_GENERIC = [
  {
    text:
      "Implement a basic LRU (least recently used) cache with get and put operations.",
    tags: ["Technical", "Design", "Hash Map"],
  },
  {
    text:
      "Given an unsorted array, return the length of the longest consecutive elements sequence.",
    tags: ["Technical", "Arrays"],
  },
  {
    text:
      "Given a matrix, return its elements in spiral order starting from the top-left corner.",
    tags: ["Technical", "Matrix"],
  },
  {
    text:
      "Given an array of meeting time intervals, determine if a single person could attend all meetings.",
    tags: ["Technical", "Intervals"],
  },
  {
    text:
      "Given a string containing parentheses, determine if the sequence is valid (properly opened and closed).",
    tags: ["Technical", "Stack"],
  },
];

// Technical – Data Scientist
const TECH_DS_GENERIC = [
  {
    text:
      "Explain how you would detect outliers in a univariate dataset. What techniques can you use?",
    tags: ["Technical", "Statistics"],
  },
  {
    text:
      "Describe the bias-variance tradeoff. How does it influence your choice of model complexity?",
    tags: ["Technical", "ML Theory"],
  },
  {
    text:
      "You have imbalanced classes in a classification problem. What approaches can you use to handle this?",
    tags: ["Technical", "ML Practice"],
  },
];

// Technical – PM or others (light system design style)
const TECH_PM_GENERIC = [
  {
    text:
      "Design a system to send notifications to millions of users when a friend posts an update. What components would you include?",
    tags: ["Technical", "System Design"],
  },
  {
    text:
      "How would you instrument a new feature to understand whether it is successful? Which metrics would you track?",
    tags: ["Technical", "Product Sense"],
  },
];

/* Helper: choose the right pool based on type / role / level / style */
function getQuestionPool(type, role, level, style) {
  const normalizedType = (type || "behavioral").toLowerCase();
  const normalizedRole = (role || "SWE").toLowerCase();
  const normalizedStyle = (style || "Generic").toLowerCase();

  if (normalizedType === "technical") {
    if (normalizedRole === "swe") {
      if (normalizedStyle === "faang") {
        return TECH_SWE_FAANG_INTERN;
      }
      return TECH_SWE_GENERIC;
    }
    if (normalizedRole === "data scientist") {
      return TECH_DS_GENERIC;
    }
    if (normalizedRole === "product manager") {
      return TECH_PM_GENERIC;
    }
    // anything else → generic SWE technical set
    return TECH_SWE_GENERIC;
  }

  // Behavioral for any role
  return BEHAVIORAL_GENERIC;
}

/* Helper: random sample from a pool */
function sampleQuestions(pool, count = 5) {
  const arr = [...pool];
  for (let i = arr.length - 1; i > 0; i--) {
    const j = Math.floor(Math.random() * (i + 1));
    [arr[i], arr[j]] = [arr[j], arr[i]];
  }
  return arr.slice(0, Math.min(count, arr.length));
}

/* ------------------------------------------------------------------ */
/* API ENDPOINTS                                                      */
/* ------------------------------------------------------------------ */

// Health check
app.get("/api/health", (_req, res) => {
  res.json({ ok: true });
});

// Main questions endpoint
// GET /api/questions?type=&role=&level=&style=
app.get("/api/questions", (req, res) => {
  const {
    type = "behavioral",
    role = "SWE",
    level = "Intern",
    style = "Generic",
  } = req.query;

  const pool = getQuestionPool(type, role, level, style);
  const questions = sampleQuestions(pool, 5);

  res.json({ questions });
});

/* ------------------------------------------------------------------ */

app.listen(PORT, () => {
  console.log(`LockedInterview API listening on http://localhost:${PORT}`);
  console.log(`Open http://localhost:${PORT}/frontend.html in your browser`);
});
