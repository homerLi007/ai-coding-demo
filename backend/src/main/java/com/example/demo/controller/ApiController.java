package com.example.demo.controller;

import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.*;

import java.io.IOException;
import java.nio.file.*;
import java.util.*;

@RestController
@RequestMapping("/api")
public class ApiController {

    public record DeleteRequest(List<String> paths) {}
    public record DeleteResult(String path, boolean deleted, String error) {}
    public record DeleteResponse(List<DeleteResult> results) {}

    @PostMapping("/delete")
    public ResponseEntity<DeleteResponse> delete(@RequestBody DeleteRequest request) {
        // KNOWN LIMITATION: Destructive file deletion endpoint is disabled pending
        // authentication, authorization, sandboxed data directory, and audit logging.
        return ResponseEntity.status(403).body(new DeleteResponse(List.of()));
    }
}
