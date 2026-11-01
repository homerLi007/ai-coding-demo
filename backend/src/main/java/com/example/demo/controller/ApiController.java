package com.example.demo.controller;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.io.IOException;
import java.nio.file.*;
import java.util.*;

@RestController
@RequestMapping("/api")
public class ApiController {

    private static final Path BASE_DIR = Paths.get(System.getProperty("user.dir")).toAbsolutePath().normalize();

    public record DeleteRequest(List<String> paths) {}
    public record DeleteResult(String path, boolean deleted, String error) {}
    public record DeleteResponse(List<DeleteResult> results) {}

    @PostMapping("/delete")
    public ResponseEntity<DeleteResponse> delete(@RequestBody DeleteRequest request) {
        List<DeleteResult> results = new ArrayList<>();
        if (request == null || request.paths() == null) {
            return ResponseEntity.ok(new DeleteResponse(results));
        }
        for (String raw : request.paths()) {
            results.add(deleteOne(raw));
        }
        return ResponseEntity.ok(new DeleteResponse(results));
    }

    private DeleteResult deleteOne(String raw) {
        if (raw == null || raw.isBlank()) {
            return new DeleteResult(raw, false, "empty path");
        }
        try {
            Path target = BASE_DIR.resolve(raw).normalize();
            if (!target.startsWith(BASE_DIR)) {
                return new DeleteResult(raw, false, "path escapes base directory");
            }
            if (!Files.exists(target, LinkOption.NOFOLLOW_LINKS)) {
                return new DeleteResult(raw, false, "not found");
            }
            if (Files.isSymbolicLink(target)) {
                Files.delete(target);
                return new DeleteResult(raw, true, null);
            }
            if (Files.isDirectory(target)) {
                return new DeleteResult(raw, false, "is a directory");
            }
            Files.delete(target);
            return new DeleteResult(raw, true, null);
        } catch (IOException e) {
            return new DeleteResult(raw, false, e.getMessage());
        } catch (Exception e) {
            return new DeleteResult(raw, false, "error: " + e.getMessage());
        }
    }
}
