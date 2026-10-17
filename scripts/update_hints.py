"""Update all backend/challenges/*/hints.json to exactly 3 layers per ladder.

Layer 1 "Nudge": a question that points at the concept, nothing about the learner's code.
Layer 2 "Pointer": where the logic goes wrong, or what kind of input breaks it, in words.
Layer 3 "Approach": the strategy in plain language. NO code, not even partial code.
"""

import json
from pathlib import Path

HINTS_DATA = {
    "count-even": {
        "boundary_comparison": [
            "How does modular arithmetic determine whether an integer is divisible by two without any remainder?",
            "The condition breaks on numbers like zero or negative values where the remainder check fails or uses the wrong comparison.",
            "Check if dividing the number by two leaves a remainder of zero, and increment your count only when that condition holds."
        ],
        "generic": [
            "How can an algorithm inspect every number in a collection and tally only those meeting a specific condition?",
            "The counter produces an incorrect total if it starts from the wrong initial value or counts odd numbers.",
            "Initialize a tally at zero, iterate through each item in the numbers collection, test for divisibility by two, and add one for each matching number."
        ]
    },
    "count-passes": {
        "boundary_comparison": [
            "Are scores that equal the exact passing threshold considered passing marks?",
            "The logic fails when a student scores exactly forty because a strict greater-than check ignores boundary scores.",
            "Use an inclusive comparison so that marks equal to or exceeding the passing threshold are counted."
        ],
        "off_by_one": [
            "How should a counter initialize and advance as it iterates over matching elements?",
            "The count is off by one if the accumulator begins at a non-zero value or counts before verifying the score.",
            "Start an accumulator at zero, evaluate each mark against the pass threshold, and increment the accumulator once per qualifying student."
        ],
        "print_not_return": [
            "What is the difference between sending a value back to a caller and printing it to the console?",
            "The test runner receives no result because writing text to the terminal leaves the function returning nothing.",
            "Pass the final integer count back to the caller using a return statement instead of printing to the terminal."
        ],
        "generic": [
            "How does a program systematically evaluate student grades against a required threshold?",
            "The logic fails on empty lists or when scores land right on the passing boundary.",
            "Traverse the sequence of marks, test whether each score meets the passing cutoff, and return the accumulated count."
        ]
    },
    "first-n-squares": {
        "off_by_one": [
            "When counting the first positive integers, where does counting begin and where does it end?",
            "The generated sequence starts at zero instead of one, or stops before reaching the square of the target number.",
            "Generate numbers starting from one through the requested count inclusive, squaring each integer and collecting them into an ordered list."
        ],
        "unhandled_short_input": [
            "What should a collection of the first positive integers look like when zero numbers are requested?",
            "The function fails when given zero or negative input by generating unwanted numbers instead of an empty collection.",
            "Immediately produce an empty list when the requested count is zero or negative."
        ],
        "print_not_return": [
            "Does the caller receive the list as a result, or does it only appear in the terminal output?",
            "The grader evaluates the return value as nothing because each square is printed rather than returned.",
            "Accumulate the squared integers into a list and provide that completed list as the function return value."
        ],
        "generic": [
            "How can an algorithm build a list containing square numbers for a specified count of terms?",
            "The logic misbehaves on zero inputs or when the sequence range excludes the last desired number.",
            "Iterate from one through the target number inclusive, calculate each square, store the values in sequence, and return the final list."
        ]
    },
    "group-passing-marks": {
        "boundary_comparison": [
            "Which bucket should receive a score that lands exactly on the passing boundary?",
            "Scores of exactly forty are placed into the failing group instead of the passing group.",
            "Ensure that marks meeting or exceeding forty are appended to the passing list, while lower marks are appended to the failing list."
        ],
        "generic": [
            "How can key-value mappings categorize records into separate buckets while preserving order?",
            "The structure breaks when input contains no records or when items are sorted instead of preserving insertion order.",
            "Create two separate lists for passing and failing names, check each student score, place each student into the appropriate list in order, and assemble them into the result dictionary."
        ]
    },
    "last-k-total": {
        "unhandled_short_input": [
            "What should happen when the requested window is larger than the entire list?",
            "When the requested count exceeds the list length, computing a negative starting offset wraps around and adds items multiple times.",
            "Ensure the starting position never drops below zero by taking the entire list when the requested count exceeds available items."
        ],
        "off_by_one": [
            "How many items should be included in a total from the tail of a sequence?",
            "The slice or loop selects one element too few or one element too many from the end of the collection.",
            "Determine the starting boundary from the right-hand side using the requested count and sum exactly those tail elements."
        ],
        "print_not_return": [
            "How does a caller obtain the calculated total from a function?",
            "The function calculates the correct sum but displays it to the screen instead of returning it to the program.",
            "Return the resulting sum integer directly to the caller rather than writing it to standard output."
        ],
        "generic": [
            "How can a program safely extract and sum a subset of numbers from the end of a list?",
            "The logic miscalculates when the list is empty, when the count is zero, or when the count is larger than the list.",
            "Handle boundary inputs where the count is zero or the list is empty, isolate the tail segment, and sum the elements."
        ]
    },
    "middle-three": {
        "unhandled_short_input": [
            "How should an extraction function respond when a list contains fewer items than the requested window size?",
            "The logic crashes with an index error when given a list with zero, one, or two elements.",
            "Check the size of the input list first, and return an empty list or the entire input if it contains fewer than three elements."
        ],
        "off_by_one": [
            "In an odd-length sequence, how is the exact midpoint defined and how do neighbors flank it?",
            "The window is shifted left or right, missing the true center or picking an asymmetric set of neighbors.",
            "Find the center position using integer division by two, and extract a window spanning from one position before the center through one position after."
        ],
        "mutates_input": [
            "Should inspecting a sequence alter the caller's original list of items?",
            "Calling removal or truncation operations on the input modifies the caller's data in place.",
            "Extract the middle elements using a non-destructive slice or new list creation so the original collection remains untouched."
        ],
        "generic": [
            "How do you locate the center of a list and extract a balanced window around it?",
            "Calculations fail when the list is too short or when integer division rounds the center index unexpectedly.",
            "Verify that the list has at least three elements, calculate the center index, and slice the middle three elements into a new list."
        ]
    },
    "remove-duplicates": {
        "mutates_input": [
            "What does it mean for a function to be non-destructive towards its arguments?",
            "Modifying or popping elements from the input list alters the caller's original data.",
            "Construct a fresh list to store distinct elements, leaving the input sequence completely unmodified."
        ],
        "generic": [
            "How can a program preserve the first appearance of each item while ignoring repeated occurrences?",
            "Converting directly to an unordered collection loses the original sequence order.",
            "Maintain a tracking set of previously encountered values, iterate through the input in order, and append each newly seen element to a results list."
        ]
    },
    "rotate-list": {
        "off_by_one": [
            "When shifting items from the back of a list to the front, which elements move and which stay?",
            "The split point takes one element too many or too few, altering the rotation boundary.",
            "Divide the sequence into the tail segment that shifts forward and the head segment that shifts back, then concatenate them in rotated order."
        ],
        "generic": [
            "What happens when a rotation amount is larger than the length of the list itself?",
            "A rotation count greater than the list size causes index errors or full wraparound miscalculations.",
            "Calculate the effective rotation by taking the rotation count modulo the list length, then slice and reassemble the two halves."
        ]
    },
    "running-total": {
        "mutates_input": [
            "Should calculating cumulative statistics modify the numbers in the original container?",
            "Overwriting elements in the input list changes the caller's data and can corrupt subsequent running calculations.",
            "Build a new list of partial sums step by step rather than updating the input list in place."
        ],
        "generic": [
            "How does an accumulator variable capture a progressive sequence of cumulative totals?",
            "The accumulation fails on an empty list or when the running total resets prematurely.",
            "Initialize a cumulative sum at zero, iterate through each number adding it to the sum, and append each progressive sum to a new output list."
        ]
    },
    "safe-first-element": {
        "unhandled_short_input": [
            "What occurs when accessing an index in a collection that contains zero items?",
            "Attempting to index the first position on an empty list raises an out-of-range error before any fallback can occur.",
            "Check if the collection has any elements before accessing index zero; return the provided default value if the collection is empty."
        ],
        "generic": [
            "How can a function safely guard against empty container access while returning a default value?",
            "The code unconditionally retrieves the first item and crashes when given an empty list.",
            "Test whether the input collection is non-empty; return the first element if present, otherwise return the fallback default value."
        ]
    }
}

def main():
    base_dir = Path(__file__).resolve().parent.parent / "backend" / "challenges"
    for ch_id, ladder_map in HINTS_DATA.items():
        hints_file = base_dir / ch_id / "hints.json"
        assert hints_file.exists(), f"File does not exist: {hints_file}"
        with open(hints_file, "w", encoding="utf-8") as f:
            json.dump(ladder_map, f, indent=2)
            f.write("\n")
        print(f"Updated {hints_file} with {len(ladder_map)} ladders (3 hints each)")

if __name__ == "__main__":
    main()
