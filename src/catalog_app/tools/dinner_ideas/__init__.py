"""A deterministic, weekday-alliterative 90-day dinner rotation."""

from datetime import date, datetime, timedelta
import re
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field

# Thirteen distinct ideas for each weekday cover any 90-day date range.
# Tuesday and Thursday, and Saturday and Sunday, have separate menus.
MENUS: tuple[tuple[tuple[str, str], ...], ...] = (
    (
        ("Meatloaf", "Serve with mashed potatoes and green beans."),
        ("Mushroom risotto", "Finish with Parmesan and a green salad."),
        ("Mediterranean chicken bowls", "Add couscous, cucumber, and tzatziki."),
        ("Macaroni and cheese", "Pair with roasted broccoli."),
        ("Maple-mustard salmon", "Serve with brown rice and asparagus."),
        ("Mexican stuffed peppers", "Fill with beans, rice, and cheese."),
        ("Minestrone", "Serve with crusty bread."),
        ("Miso-glazed tofu", "Add rice and steamed bok choy."),
        ("Moroccan chickpea stew", "Serve over couscous."),
        ("Meatball subs", "Add marinara, mozzarella, and a side salad."),
        ("Mustard pork chops", "Pair with roasted potatoes and carrots."),
        ("Mushroom stroganoff", "Spoon over egg noodles."),
        ("Mango chicken", "Serve with coconut rice and snap peas."),
    ),
    (
        ("Tacos", "Fill with seasoned beef or beans, lettuce, and salsa."),
        ("Turkey burgers", "Add oven fries and a crunchy slaw."),
        ("Teriyaki chicken", "Serve with rice and broccoli."),
        ("Tomato basil pasta", "Add white beans and Parmesan."),
        ("Tostadas", "Top with refried beans, avocado, and salsa."),
        ("Tuna melts", "Serve with tomato soup or a side salad."),
        ("Tortellini soup", "Add spinach and a tomato broth."),
        ("Tempeh lettuce wraps", "Add crunchy vegetables and peanut sauce."),
        ("Trout with lemon butter", "Serve with potatoes and peas."),
        ("Turkish lentil soup", "Pair with flatbread and cucumber salad."),
        ("Tandoori chicken", "Serve with rice and cucumber yogurt."),
        ("Tamale pie", "Bake a bean-and-beef filling under cornbread."),
        ("Tuscan white bean skillet", "Add kale, tomatoes, and crusty bread."),
    ),
    (
        ("Wok-fried chicken", "Toss with vegetables and serve over rice."),
        ("White bean chili", "Top with avocado and serve with cornbread."),
        ("Whole-wheat pesto pasta", "Add zucchini and cherry tomatoes."),
        ("Walnut-crusted salmon", "Serve with quinoa and roasted carrots."),
        ("Wonton soup", "Add bok choy and mushrooms."),
        ("Waldorf chicken salad wraps", "Fill wraps with chicken, apples, and celery."),
        ("Wild mushroom pizza", "Top with mozzarella and thyme."),
        ("Wok-seared tofu noodles", "Toss with vegetables and sesame sauce."),
        ("White fish tacos", "Add cabbage slaw and lime crema."),
        ("Wheat berry grain bowls", "Add roasted vegetables and chickpeas."),
        ("White chicken lasagna", "Layer with spinach and a creamy sauce."),
        ("Warm lentil salad", "Add roasted sweet potatoes and feta."),
        ("Welsh rarebit", "Serve cheese-topped toast with tomato soup."),
    ),
    (
        ("Tikka masala", "Use chicken or chickpeas; serve with rice and naan."),
        ("Thai green curry", "Add tofu or chicken, vegetables, and jasmine rice."),
        ("Tarragon chicken", "Serve with roasted potatoes and green beans."),
        ("Three-bean chili", "Add corn and serve with cornbread."),
        ("Tortilla soup", "Top with avocado and crispy tortilla strips."),
        ("Tagine with apricots", "Use chicken or chickpeas and serve with couscous."),
        ("Tteokbokki", "Add tofu and vegetables to spicy Korean rice cakes."),
        ("Thai peanut noodles", "Add edamame, carrots, and cucumber."),
        ("Tuna noodle casserole", "Add peas and a crunchy breadcrumb topping."),
        ("Tomato-stuffed chicken", "Fill with sun-dried tomatoes and spinach."),
        ("Tri-tip roast", "Serve with potatoes and roasted vegetables."),
        ("Tofu scramble burritos", "Fill with black beans, peppers, and salsa."),
        ("Tunisian chickpea stew", "Add cumin, lemon, and toasted bread."),
    ),
    (
        ("Fried fish", "Serve with oven fries, slaw, and lemon."),
        ("Fajitas", "Sizzle chicken or beans with peppers and onions."),
        ("Falafel bowls", "Add rice, hummus, cucumber, and tahini."),
        ("Fettuccine Alfredo", "Add broccoli and chicken or white beans."),
        ("French onion soup", "Top with cheesy toast and add a side salad."),
        ("Fish pie", "Bake fish and peas under mashed potatoes."),
        ("Frittata", "Add potatoes, spinach, and cheese; serve with salad."),
        ("Flatbread pizza", "Top with roasted vegetables and mozzarella."),
        ("Firecracker tofu", "Serve with rice and steamed vegetables."),
        ("Florentine chicken", "Serve creamy spinach chicken over pasta."),
        ("Fried rice", "Add eggs, peas, carrots, and tofu or shrimp."),
        ("French lentil stew", "Add carrots and herbs; serve with bread."),
        ("Five-spice pork", "Serve with noodles and stir-fried cabbage."),
    ),
    (
        ("Sloppy joes", "Use beef or lentils; serve with sweet potato wedges."),
        ("Spaghetti and meatballs", "Add marinara and a green salad."),
        ("Sheet-pan sausage", "Roast with potatoes, peppers, and onions."),
        ("Sushi bowls", "Use cooked salmon or tofu, rice, cucumber, and nori."),
        ("Sweet potato black bean enchiladas", "Top with salsa and cheese."),
        ("Shrimp scampi", "Serve over pasta with a lemony salad."),
        ("Spinach stuffed shells", "Bake with ricotta and marinara."),
        ("Sesame chicken", "Serve with rice and stir-fried broccoli."),
        ("Smash burgers", "Add fries and sliced tomatoes."),
        ("Souvlaki skewers", "Serve with pita, tzatziki, and a Greek salad."),
        ("Smoky lentil tacos", "Add cabbage, avocado, and salsa."),
        ("Steak fajita bowls", "Add rice, peppers, onions, and lime."),
        ("Soba noodle stir-fry", "Add edamame and vegetables."),
    ),
    (
        ("Sofrito chicken", "Simmer in sofrito and serve with rice and beans."),
        ("Shepherd's pie", "Bake lamb and vegetables under mashed potatoes."),
        ("Sunday pot roast", "Cook with potatoes, carrots, and onions."),
        ("Salmon rice bowls", "Add cucumber, avocado, and a sesame dressing."),
        ("Stuffed cabbage rolls", "Fill with beef or lentils and rice."),
        ("Shakshuka", "Serve eggs in spiced tomato sauce with pita."),
        ("Slow-cooker pulled pork", "Serve on buns with slaw."),
        ("Spinach chickpea curry", "Serve with rice and naan."),
        ("Seafood paella", "Add shrimp, peas, and saffron rice."),
        ("Smothered pork chops", "Add onion gravy, potatoes, and green beans."),
        ("Sausage and white bean stew", "Add kale and crusty bread."),
        ("Sweet-and-sour tofu", "Serve with rice, peppers, and pineapple."),
        ("Stuffed portobello mushrooms", "Fill with quinoa, spinach, and cheese."),
    ),
)
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
DEFAULT_CYCLE_START = "2026-10-26"


def _parse_date(value: str, field: str) -> date:
    if not isinstance(value, str) or not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", value):
        raise ValueError(f"{field} must be a date in YYYY-MM-DD format.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{field} must be a valid calendar date.") from exc


def dinner_ideas(
    for_date: str = "",
    days: Annotated[int, Field(strict=True, ge=1, le=90)] = 1,
    cycle_start: str = DEFAULT_CYCLE_START,
    time_zone: str = "America/New_York",
) -> dict[str, Any]:
    """Get daily dinner ideas in a repeating 90-day, weekday-letter rotation.

    Monday meals start with M; Tuesday and Thursday with T; Wednesday with W;
    Friday with F; Saturday and Sunday with S. Each weekday has 13 distinct
    ideas, including meatloaf, tacos, wok-fried chicken, tikka masala, fried fish,
    sloppy joes, and sofrito chicken in the first cycle week.

    for_date: YYYY-MM-DD; omit or use an empty string for today in time_zone.
    days: 1 for a daily idea, 7 for a week, or 90 for a full plan (range 1-90).
    cycle_start: YYYY-MM-DD anchor, default Monday 2026-10-26. Keep the same
    anchor across calls to advance consistently. Dates before it are rejected.
    After day 90 the menu rotation restarts, always matching the actual weekday.
    Since 90 is not a multiple of 7, subsequent cycles use the same weekday
    menus, not an identical sequence of meal names. A Monday anchor gives the
    example week above; other anchors still respect actual weekdays.
    time_zone: IANA zone used to resolve today, default America/New_York.

    Returns dated dinners with their letter, serving idea, cycle day and week.
    Read-only: no account, credentials, storage, or automatic notifications.
    Suggestions are not allergy-screened. Schedule delivery separately if wanted.
    """
    if type(days) is not int or not 1 <= days <= 90:
        raise ValueError("days must be a whole number between 1 and 90.")
    if not isinstance(time_zone, str) or not time_zone:
        raise ValueError("time_zone must be a valid IANA time zone.")
    try:
        zone = ZoneInfo(time_zone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ValueError("time_zone must be a valid IANA time zone, such as America/New_York.") from exc
    start = _parse_date(cycle_start, "cycle_start")
    requested = datetime.now(zone).date() if for_date == "" else _parse_date(for_date, "for_date")
    if requested < start:
        raise ValueError("for_date must be on or after cycle_start; choose an earlier cycle_start.")
    try:
        requested + timedelta(days=days - 1)
    except OverflowError as exc:
        raise ValueError("The requested date range extends beyond year 9999.") from exc

    dinners = []
    for offset in range(days):
        current = requested + timedelta(days=offset)
        elapsed = (current - start).days
        cycle_day = elapsed % 90 + 1
        menu_index = (cycle_day - 1) // 7
        weekday = current.weekday()
        name, serving_idea = MENUS[weekday][menu_index]
        dinners.append({
            "date": current.isoformat(),
            "weekday": WEEKDAYS[weekday],
            "letter": WEEKDAYS[weekday][0],
            "dinner": name,
            "serving_idea": serving_idea,
            "cycle_number": elapsed // 90 + 1,
            "cycle_day": cycle_day,
            "cycle_week": menu_index + 1,
        })
    return {
        "cycle_start": start.isoformat(),
        "cycle_length_days": 90,
        "time_zone": time_zone,
        "dinners": dinners,
        "delivery": "On demand. This tool does not send automatic daily notifications.",
    }
