import os
import sys
import getpass
import argparse
from typing import List, Optional
from dotenv import load_dotenv
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, BarColumn, TextColumn, DownloadColumn, TransferSpeedColumn, TimeRemainingColumn
from rich.panel import Panel
from rich.prompt import Prompt, Confirm

# Fix Windows console UTF-8 encoding
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding='utf-8', errors='replace')
        sys.stderr.reconfigure(encoding='utf-8', errors='replace')
    except Exception:
        pass

from lms_client import LMSClient, sanitize_filename, CourseItem, SectionItem, FileItem

console = Console(highlight=False)

def load_credentials() -> tuple[Optional[str], Optional[str]]:
    load_dotenv()
    username = os.getenv("LMS_USERNAME")
    password = os.getenv("LMS_PASSWORD")
    return username, password

def save_credentials(username: str, password: str):
    with open(".env", "w", encoding="utf-8") as f:
        f.write(f"LMS_USERNAME={username}\n")
        f.write(f"LMS_PASSWORD={password}\n")
    # Ensure .env is gitignored
    if os.path.exists(".gitignore"):
        with open(".gitignore", "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
        if ".env" not in lines:
            with open(".gitignore", "a", encoding="utf-8") as f:
                f.write("\n.env\n")
    else:
        with open(".gitignore", "w", encoding="utf-8") as f:
            f.write(".env\ndownloads/\n")

def authenticate(client: LMSClient, args_user: Optional[str] = None, args_pass: Optional[str] = None) -> bool:
    env_user, env_pass = load_credentials()
    username = args_user or env_user
    password = args_pass or env_pass

    if not username:
        username = Prompt.ask("[bold cyan]Enter LMS Username/Roll No[/bold cyan]")
    if not password:
        password = getpass.getpass("Enter LMS Password: ")

    console.print("[dim]Logging in to IIIT Kottayam LMS...[/dim]")
    success, msg = client.login(username, password)

    if not success:
        console.print(f"[bold red]✗ {msg}[/bold red]")
        return False

    console.print(f"[bold green]✓ Successfully logged in as [bold white]{username}[/bold white]![/bold green]")
    
    # If credentials were manually entered and not in .env, offer to save
    if not env_user and not args_user:
        save_env = Confirm.ask("Do you want to remember credentials in local .env?", default=False)
        if save_env:
            save_credentials(username, password)
            console.print("[dim]Credentials saved to .env (gitignored).[/dim]")

    return True

def display_courses_table(courses: List[CourseItem], title: str = "🎓 Enrolled LMS Courses"):
    table = Table(title=title, show_header=True, header_style="bold magenta")
    table.add_column("#", style="cyan", width=4, justify="right")
    table.add_column("Course ID", style="dim", width=12)
    table.add_column("Year", style="yellow", width=6, justify="center")
    table.add_column("Course Name", style="bold white")

    for idx, course in enumerate(courses, 1):
        year_str = f"Yr {course.year}" if course.year else "-"
        table.add_row(str(idx), course.id, year_str, course.name)

    console.print(table)

def download_course_materials(client: LMSClient, course: CourseItem, output_dir: str, use_zip: bool = False, dry_run: bool = False, is_offline: bool = False):
    console.print(f"\n[bold yellow]Scanning Course:[/bold yellow] [bold white]{course.name}[/bold white] (ID: {course.id})")
    
    if is_offline:
        if os.path.exists("a_course.html") and course.id == "141":
            with open("a_course.html", "r", encoding="utf-8") as f:
                sections = client.parse_course_page_from_html(f.read(), course.id)
        else:
            sections = []
    else:
        with console.status("[dim]Fetching course sections and activities...[/dim]"):
            sections = client.get_course_sections(course.id)

    if not sections:
        console.print("[yellow]No accessible sections or materials found in this course.[/yellow]")
        return

    clean_course_name = sanitize_filename(course.name)
    course_dir = os.path.join(output_dir, clean_course_name)

    stats = {"downloaded": 0, "skipped": 0, "failed": 0}

    for sec in sections:
        clean_sec_name = sanitize_filename(sec.title)
        
        for act in sec.activities:
            if act.type == "folder":
                act_dir = os.path.join(course_dir, clean_sec_name, sanitize_filename(act.name))
                console.print(f"\n📁 [bold cyan]Folder:[/bold cyan] {act.name} ([dim]Section: {sec.title}[/dim])")
                
                if use_zip:
                    # Download whole folder as ZIP
                    if dry_run or is_offline:
                        console.print(f"  [dim][PREVIEW] Folder ZIP URL: https://lmsug24.iiitkottayam.ac.in/mod/folder/download_folder.php?id={act.id}[/dim]")
                        console.print(f"  [dim]Target destination: {act_dir}[/dim]")
                        continue
                    console.print(f"  [dim]Downloading folder ZIP...[/dim]")
                    res = client.download_folder_zip(act.id, os.path.join(course_dir, clean_sec_name), act.name, extract=True)
                    if res:
                        stats["downloaded"] += 1
                        console.print(f"  [green]✓ Extracted folder to {res}[/green]")
                    else:
                        stats["failed"] += 1
                else:
                    # Download individual files inside folder
                    if is_offline and os.path.exists("materials_folder.html") and act.id == "5390":
                        with open("materials_folder.html", "r", encoding="utf-8") as f:
                            files = client.parse_folder_files_from_html(f.read())
                    else:
                        with console.status("  [dim]Fetching folder contents...[/dim]"):
                            files = client.get_folder_files(act.id)
                    
                    if not files:
                        console.print("  [dim]Empty folder or file list not loaded.[/dim]")
                        continue

                    for f in files:
                        target_subfolder = os.path.join(act_dir, f.relative_path) if f.relative_path else act_dir
                        final_path = os.path.join(target_subfolder, sanitize_filename(f.name))
                        
                        if dry_run:
                            console.print(f"  📄 [DRY-RUN] {f.name}")
                            continue

                        # Check if file exists
                        if os.path.exists(final_path):
                            stats["skipped"] += 1
                            console.print(f"  [dim]⏭ Already exists:[/dim] {f.name}")
                            continue

                        console.print(f"  ⬇ Downloading: [white]{f.name}[/white]")
                        dl_path = client.download_file(f.url, target_subfolder, f.name)
                        if dl_path:
                            stats["downloaded"] += 1
                            console.print(f"    [green]✓ Saved to[/green] [dim]{dl_path}[/dim]")
                        else:
                            stats["failed"] += 1
                            console.print(f"    [red]✗ Failed to download {f.name}[/red]")

            elif act.type == "resource":
                # Single file resource
                target_sec_dir = os.path.join(course_dir, clean_sec_name)
                console.print(f"\n📄 [bold cyan]Resource:[/bold cyan] {act.name}")
                if dry_run:
                    console.print(f"  [DRY-RUN] Would download single resource to: {target_sec_dir}")
                    continue

                dl_path = client.download_single_resource(act.id, target_sec_dir, act.name)
                if dl_path:
                    stats["downloaded"] += 1
                    console.print(f"  [green]✓ Saved to[/green] [dim]{dl_path}[/dim]")
                else:
                    stats["failed"] += 1
                    console.print(f"  [red]✗ Failed to download resource {act.name}[/red]")

    console.print(f"\n[bold green]Course Summary ({course.name}):[/bold green] "
                  f"Downloaded: [bold cyan]{stats['downloaded']}[/bold cyan], "
                  f"Skipped: [bold yellow]{stats['skipped']}[/bold yellow], "
                  f"Failed: [bold red]{stats['failed']}[/bold red]")

def main():
    parser = argparse.ArgumentParser(description="IIIT Kottayam LMS Material Downloader & Automator")
    parser.add_argument("-u", "--username", help="LMS Username / Roll Number")
    parser.add_argument("-p", "--password", help="LMS Password")
    parser.add_argument("-a", "--all", action="store_true", help="Download materials for all enrolled courses")
    parser.add_argument("-c", "--course", help="Specify Course ID or partial Course Name to download")
    parser.add_argument("-o", "--output", default="downloads", help="Destination folder (default: ./downloads)")
    parser.add_argument("-l", "--list", action="store_true", help="Only list enrolled courses and exit")
    parser.add_argument("-y", "--year", type=int, choices=[1, 2, 3, 4], help="Filter courses by year of study (1, 2, 3, or 4)")
    parser.add_argument("--use-zip", action="store_true", help="Download folders as ZIP archives instead of individual files")
    parser.add_argument("--dry-run", action="store_true", help="Scan and list files without actually downloading")
    parser.add_argument("--offline", action="store_true", help="Simulate and test extraction using saved local HTML files without network")
    parser.add_argument("--debug", action="store_true", help="Enable verbose debug logging")
    args = parser.parse_args()

    console.print(Panel.fit(
        "[bold cyan]📚 IIIT Kottayam LMS Downloader & Automator[/bold cyan]\n"
        "[dim]Automatically downloads course slides, materials, folders, and resources.[/dim]",
        border_style="cyan"
    ))

    client = LMSClient(debug=args.debug)

    if args.offline:
        console.print("[bold yellow]ℹ Running in OFFLINE mode using local sample HTML files.[/bold yellow]")
        if not os.path.exists("courses.html"):
            console.print("[red]courses.html not found in current directory.[/red]")
            sys.exit(1)
        with open("courses.html", "r", encoding="utf-8") as f:
            courses = client.parse_courses_from_html(f.read())
    else:
        if not authenticate(client, args.username, args.password):
            sys.exit(1)

        with console.status("[dim]Fetching your courses...[/dim]"):
            courses = client.get_courses()

    if not courses:
        console.print("[bold red]No enrolled courses found or unable to fetch course list.[/bold red]")
        console.print("[yellow]Tip: Run with --debug to see detailed response logs.[/yellow]")
        sys.exit(1)

    # Year selection and course filtering loop
    active_courses: Optional[List[CourseItem]] = None
    table_title = "🎓 Enrolled LMS Courses"

    if args.year:
        filtered = [c for c in courses if c.year == args.year]
        if not filtered:
            console.print(f"[bold red]No courses found for Year {args.year}.[/bold red]")
            sys.exit(1)
        active_courses = filtered
        table_title = f"🎓 Year {args.year} Courses"

    while True:
        if active_courses is None:
            # Group courses by year
            year_groups = {}
            for c in courses:
                y = c.year or "Other"
                year_groups.setdefault(y, []).append(c)

            console.print("\n[bold cyan]🎓 Select Year of Study:[/bold cyan]")
            available_years = sorted([k for k in year_groups if isinstance(k, int)])
            for y in available_years:
                console.print(f"  [bold white][{y}][/bold white] Year {y} ({len(year_groups[y])} courses)")
            if "Other" in year_groups:
                console.print(f"  [bold white][o][/bold white] Other / General ({len(year_groups['Other'])} courses)")
            console.print(f"  [bold white][a][/bold white] Show All Courses ({len(courses)} courses)")
            console.print(f"  [bold red][q][/bold red] Quit\n")

            default_choice = str(max(available_years)) if available_years else "a"
            y_choice = Prompt.ask("[bold green]Choose Year of Study[/bold green]", default=default_choice).lower().strip()

            if y_choice == 'q':
                console.print("Exiting.")
                sys.exit(0)
            elif y_choice == 'a':
                active_courses = courses
                table_title = "🎓 All Enrolled LMS Courses"
            elif y_choice == 'o':
                active_courses = year_groups.get("Other", [])
                table_title = "🎓 Other / General Courses"
            elif y_choice.isdigit() and int(y_choice) in year_groups:
                y_num = int(y_choice)
                active_courses = year_groups[y_num]
                table_title = f"🎓 Year {y_num} Courses"
            else:
                console.print("[yellow]Invalid option, please choose a valid year or 'a'.[/yellow]")
                continue

        display_courses_table(active_courses, title=table_title)

        if args.list:
            sys.exit(0)

        selected_courses: List[CourseItem] = []

        if args.all:
            selected_courses = active_courses
            break
        elif args.course:
            target = args.course.lower().strip()
            selected_courses = [c for c in active_courses if target == c.id.lower() or target in c.name.lower()]
            if not selected_courses:
                console.print(f"[bold red]No course matched '{args.course}' in this year.[/bold red]")
                sys.exit(1)
            break
        else:
            # Interactive selection within the selected year
            console.print(f"\n[bold]Options for {table_title}:[/bold]")
            console.print(f"  • Type [bold cyan]all[/bold cyan] to download all {len(active_courses)} courses")
            console.print("  • Type course numbers separated by commas (e.g. [bold cyan]1, 3[/bold cyan])")
            console.print("  • Type [bold yellow]b[/bold yellow] to go back and choose another Year")
            console.print("  • Type [bold red]q[/bold red] to quit\n")
            
            choice = Prompt.ask("[bold green]Select courses to download[/bold green]", default="all").strip()
            if choice.lower() == 'q':
                console.print("Exiting.")
                sys.exit(0)
            elif choice.lower() == 'b':
                active_courses = None
                continue
            elif choice.lower() == 'all':
                selected_courses = active_courses
                break
            else:
                try:
                    indices = [int(x.strip()) for x in choice.split(",") if x.strip()]
                    selected = []
                    for idx in indices:
                        if 1 <= idx <= len(active_courses):
                            selected.append(active_courses[idx - 1])
                        else:
                            console.print(f"[yellow]Warning: Index {idx} out of range.[/yellow]")
                    if selected:
                        selected_courses = selected
                        break
                    else:
                        console.print("[yellow]No valid courses selected.[/yellow]")
                except ValueError:
                    console.print("[red]Invalid selection format.[/red]")

    if not selected_courses:
        console.print("[yellow]No courses selected. Exiting.[/yellow]")
        sys.exit(0)

    os.makedirs(args.output, exist_ok=True)
    console.print(f"\n[bold green]Preparing to download {len(selected_courses)} course(s) to '{args.output}'...[/bold green]")

    try:
        for course in selected_courses:
            download_course_materials(client, course, args.output, use_zip=args.use_zip, dry_run=args.dry_run, is_offline=args.offline)
        console.print(Panel.fit("[bold green]✨ All selected courses processed successfully![/bold green]", border_style="green"))
    except KeyboardInterrupt:
        console.print("\n[yellow]⚠️ Download interrupted by user.[/yellow]")
        sys.exit(0)

if __name__ == "__main__":
    main()
