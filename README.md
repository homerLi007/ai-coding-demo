def calculate_average(numbers):
    if numbers is None:
        raise ValueError("Input cannot be None")
    if not isinstance(numbers, (list, tuple)):
        raise TypeError("Input must be a list or tuple of numbers")
    if len(numbers) == 0:
        raise ValueError("Input list cannot be empty")
    for n in numbers:
        if isinstance(n, bool) or not isinstance(n, (int, float)):
            raise TypeError("All elements must be numbers")
    total = 0
    for n in numbers:
        total += n
    return total / len(numbers)