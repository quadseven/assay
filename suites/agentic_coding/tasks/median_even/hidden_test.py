from stats import median


def test_median_even_averages_the_middle_pair():
    assert median([1, 2, 3, 4]) == 2.5


def test_median_odd_still_works():
    assert median([3, 1, 2]) == 2
