import os
import re
import stat
import textwrap
from xml.sax.saxutils import escape as xml_escape


_JAVA_PKG = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")

_JAVA_KEYWORDS = frozenset({
    "abstract", "assert", "boolean", "break", "byte", "case", "catch",
    "char", "class", "const", "continue", "default", "do", "double",
    "else", "enum", "extends", "final", "finally", "float", "for",
    "goto", "if", "implements", "import", "instanceof", "int",
    "interface", "long", "native", "new", "package", "private",
    "protected", "public", "return", "short", "static", "strictfp",
    "super", "switch", "synchronized", "this", "throw", "throws",
    "transient", "try", "void", "volatile", "while", "true", "false",
    "null", "var", "record", "yield", "sealed", "permits", "non-sealed",
})


_O_DIR_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)


def _validate_package_name(package_name: str) -> None:
    if not package_name or not _JAVA_PKG.fullmatch(package_name):
        raise ValueError(f"Invalid Java package name: {package_name!r}")
    for segment in package_name.split("."):
        if segment in _JAVA_KEYWORDS:
            raise ValueError(
                f"Invalid Java package name: {package_name!r} "
                f"(segment {segment!r} is a reserved keyword)"
            )


def _open_dir_nofollow(path: str) -> int:
    """Open a directory without following a symlink at the final component."""
    return os.open(path, _O_DIR_FLAGS)


def _verify_dir_fd(fd: int, name: str) -> None:
    """Explicitly verify the fd refers to a directory owned by the current euid.

    This is portable across platforms where O_DIRECTORY may be unavailable
    (getattr fallback to 0), and rejects pre-existing entries that are not
    directories or that are owned by another user.
    """
    st = os.fstat(fd)
    if not stat.S_ISDIR(st.st_mode):
        raise ValueError(f"Path component {name!r} is not a directory")
    if hasattr(os, "geteuid") and st.st_uid != os.geteuid():
        raise ValueError(
            f"Path component {name!r} is not owned by the current user"
        )


def _mkdir_at(parent_fd: int, name: str) -> int:
    """Create (or open) a subdirectory relative to parent_fd, rejecting symlinks."""
    try:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
    except FileExistsError:
        pass
    fd = os.open(name, _O_DIR_FLAGS, dir_fd=parent_fd)
    try:
        _verify_dir_fd(fd, name)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _safe_write_at(dir_fd: int, name: str, content: str) -> None:
    """Atomically write content into a file relative to dir_fd.

    Writes to a temp file in the same directory, fsyncs, then os.replace()s
    into place. On any failure the temp file is unlinked and the directory is
    fsynced so retries are safe and no residue is left behind.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    tmp_name = f".{name}.tmp.{os.getpid()}.{os.urandom(4).hex()}"
    fd = os.open(tmp_name, flags, 0o644, dir_fd=dir_fd)
    try:
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                fd = -1
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
        finally:
            if fd != -1:
                os.close(fd)
        os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
    except BaseException:
        try:
            os.unlink(tmp_name, dir_fd=dir_fd)
        except FileNotFoundError:
            pass
        try:
            os.fsync(dir_fd)
        except OSError:
            pass
        raise


def _ensure_dir_chain(root_fd: int, components) -> int:
    """Walk/create each component under root_fd, returning the final dir fd."""
    current = root_fd
    opened = []
    success = False
    try:
        for comp in components:
            if not comp or comp in (".", "..") or "/" in comp or "\\" in comp:
                raise ValueError(f"Invalid path component: {comp!r}")
            next_fd = _mkdir_at(current, comp)
            opened.append(next_fd)
            current = next_fd
        success = True
        return current
    finally:
        if not success:
            for fd in opened:
                try:
                    os.close(fd)
                except OSError:
                    pass
        else:
            for fd in opened[:-1] if opened else []:
                try:
                    os.close(fd)
                except OSError:
                    pass


def generate_springboot_application(output_dir: str, package_name: str = "com.example.demo") -> str:
    """
    Generate a minimal, runnable Spring Boot application on disk.

    Creates a Maven project layout with a pom.xml, an application entry point,
    a sample REST controller, and application.properties. Returns the path to
    the generated project root.

    Limitations (documented, not solved here):
    - This function only writes source files; it does not invoke Maven or Java.
      Building/running requires a JDK and Maven installed on the host.
    - No dependency resolution, version pinning beyond the Spring Boot parent,
      or network access is performed.
    """
    _validate_package_name(package_name)

    if not output_dir:
        raise ValueError("output_dir must be a non-empty path")
    if os.path.islink(output_dir):
        raise ValueError("output_dir must not be a symlink")
    if not os.path.exists(output_dir):
        raise ValueError("output_dir must already exist")
    if not os.path.isdir(output_dir):
        raise ValueError("output_dir must be a directory")

    base = os.path.realpath(output_dir)
    if os.path.islink(base):
        raise ValueError("output_dir must not resolve to a symlink")

    base_fd = _open_dir_nofollow(base)
    try:
        project_fd = _mkdir_at(base_fd, "demo")
        try:
            src_fd = _mkdir_at(project_fd, "src")
            try:
                main_fd = _mkdir_at(src_fd, "main")
                try:
                    java_fd = _mkdir_at(main_fd, "java")
                    try:
                        pkg_fd = _ensure_dir_chain(java_fd, package_name.split("."))
                        try:
                            resources_fd = _mkdir_at(main_fd, "resources")
                            try:
                                pom_xml = textwrap.dedent(
                                    """\
                                    <?xml version="1.0" encoding="UTF-8"?>
                                    <project xmlns="http://maven.apache.org/POM/4.0.0"
                                             xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
                                             xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 https://maven.apache.org/xsd/maven-4.0.0.xsd">
                                        <modelVersion>4.0.0</modelVersion>
                                        <parent>
                                            <groupId>org.springframework.boot</groupId>
                                            <artifactId>spring-boot-starter-parent</artifactId>
                                            <version>3.2.5</version>
                                            <relativePath/>
                                        </parent>
                                        <groupId>{group}</groupId>
                                        <artifactId>demo</artifactId>
                                        <version>0.0.1-SNAPSHOT</version>
                                        <name>demo</name>
                                        <description>Demo Spring Boot application</description>
                                        <properties>
                                            <java.version>17</java.version>
                                        </properties>
                                        <dependencies>
                                            <dependency>
                                                <groupId>org.springframework.boot</groupId>
                                                <artifactId>spring-boot-starter-web</artifactId>
                                            </dependency>
                                        </dependencies>
                                        <build>
                                            <plugins>
                                                <plugin>
                                                    <groupId>org.springframework.boot</groupId>
                                                    <artifactId>spring-boot-maven-plugin</artifactId>
                                                </plugin>
                                            </plugins>
                                        </build>
                                    </project>
                                    """
                                ).format(group=xml_escape(package_name))

                                application_java = textwrap.dedent(
                                    """\
                                    package {pkg};

                                    import org.springframework.boot.SpringApplication;
                                    import org.springframework.boot.autoconfigure.SpringBootApplication;

                                    @SpringBootApplication
                                    public class DemoApplication {{
                                        public static void main(String[] args) {{
                                            SpringApplication.run(DemoApplication.class, args);
                                        }}
                                    }}
                                    """
                                ).format(pkg=package_name)

                                controller_java = textwrap.dedent(
                                    """\
                                    package {pkg};

                                    import org.springframework.web.bind.annotation.GetMapping;
                                    import org.springframework.web.bind.annotation.RestController;

                                    @RestController
                                    public class HelloController {{
                                        @GetMapping("/")
                                        public String hello() {{
                                            return "Hello from Spring Boot!";
                                        }}
                                    }}
                                    """
                                ).format(pkg=package_name)

                                application_properties = "server.port=8080\n"

                                _safe_write_at(project_fd, "pom.xml", pom_xml)
                                _safe_write_at(pkg_fd, "DemoApplication.java", application_java)
                                _safe_write_at(pkg_fd, "HelloController.java", controller_java)
                                _safe_write_at(resources_fd, "application.properties", application_properties)
                            finally:
                                os.close(resources_fd)
                        finally:
                            os.close(pkg_fd)
                    finally:
                        os.close(java_fd)
                finally:
                    os.close(main_fd)
            finally:
                os.close(src_fd)
        finally:
            os.close(project_fd)
    finally:
        os.close(base_fd)

    return os.path.join(base, "demo")